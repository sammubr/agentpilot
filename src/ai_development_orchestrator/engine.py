from __future__ import annotations

import json
import signal
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from . import gitops
from .agents import AgentName, run_agent
from .backlog import add_claude_dependencies, next_feature, waiting_on
from .config import Config
from .failures import FailureKind
from .gitops import GitError
from .models import (
    AgentResult,
    Feature,
    FeatureStatus,
    HistoryEvent,
    Outcome,
    PendingRecovery,
    RunStatus,
    State,
    Verdict,
    utcnow,
)
from .prompts import engineering_prompt, opencode_prompt
from .report import write_report
from .retry import decide
from .steps import (
    Step,
    block_reason,
    converge_step,
    correction_steps,
    feature_steps,
    parse_select_dependencies,
    read_spec_dir,
    read_validations,
    read_verdict,
    run_validations,
    verify,
)
from .workspace import Workspace


class StartRefused(Exception):
    pass


class Engine:
    def __init__(
        self,
        workspace: Workspace,
        config: Config,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = utcnow,
        handle_signals: bool = True,
    ) -> None:
        self.ws = workspace
        self.cfg = config
        self.sleep = sleep
        self.now = now
        self.handle_signals = handle_signals
        self._stop = False
        self._old_handlers: dict[int, object] = {}
        self.state: State | None = None
        self._last_message = ""
        self._added_dependencies: list[dict] = []

    def _log(self, event: str, feature: str | None = None, step: str | None = None, **fields: object) -> None:
        self.ws.log_event(event, feature=feature, step=step, **fields)

    def _save(self) -> None:
        if self.state is not None:
            self.state.run.updated_at = self.now()
            self.ws.save_state(self.state)

    def _on_run_end(self) -> None:
        if self.state is not None:
            if self.state.run.status == RunStatus.finished:
                self._clear_current()
            if self.state.run.status == RunStatus.paused:
                self._log("paused", feature=self.state.run.current_feature, step=self.state.run.current_step, reason=self.state.run.status_reason)
            elif self.state.run.status == RunStatus.stopped:
                self._log("stopped", feature=self.state.run.current_feature, step=self.state.run.current_step, reason=self.state.run.status_reason)
            self._save()
            write_report(self.ws, self.state)

    def _on_signal(self, signum: int, frame: object) -> None:
        self._stop = True

    def run(self, max_features: int | None = None) -> RunStatus:
        self.ws.acquire_lock()
        self.state = self.ws.load_state()
        started = False
        try:
            if self.handle_signals:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    self._old_handlers[sig] = signal.signal(sig, self._on_signal)
            self._start_gate()
            started = True
            self.state.run.status = RunStatus.running
            self.state.run.started_at = self.state.run.started_at or self.now()
            self._save()
            self._log("run_started")
            pending = self.state.run.pending_recovery
            if pending:
                delay = (pending.next_retry_at - self.now()).total_seconds()
                outcome = self._wait(max(delay, 0))
                if outcome is not None:
                    self.state.run.status = RunStatus(outcome.value)
                    self._save()
                    return self.state.run.status
            return self._run_loop(max_features)
        finally:
            try:
                if started:
                    self._log("run_ended")
                    self._on_run_end()
            finally:
                for sig, handler in self._old_handlers.items():
                    signal.signal(sig, handler)
                self.ws.release_lock()

    def _start_gate(self) -> None:
        if self.state is None or self.cfg.git.allow_dirty_start:
            return
        if self.state.run.active_step:
            return
        if self.state.run.status == RunStatus.paused and self.state.run.current_feature:
            return
        dirty = gitops.dirty_files(self.ws.repo)
        if dirty:
            raise StartRefused("working tree has uncommitted changes: " + ", ".join(dirty))

    def _run_loop(self, max_features: int | None) -> RunStatus:
        completed_count = 0
        while self.state is not None:
            outcome = self._control_outcome()
            if outcome is not None:
                self.state.run.status = RunStatus(outcome.value)
                self._save()
                return self.state.run.status
            reason = self._halt_reason()
            if reason:
                self.state.run.status = RunStatus.halted
                self.state.run.status_reason = reason
                self._save()
                return self.state.run.status
            feature = next_feature(self.state)
            if feature is None:
                return self._finish_empty_or_waiting()
            outcome = self._run_feature(feature)
            if outcome == Outcome.paused or outcome == Outcome.stopped:
                self.state.run.status = RunStatus(outcome.value)
                self._save()
                return self.state.run.status
            current = self._feature(feature.id)
            if current and current.status == FeatureStatus.completed:
                completed_count += 1
                if max_features is not None and completed_count >= max_features:
                    self.state.run.status = RunStatus.idle
                    self.state.run.status_reason = "max features reached"
                    self._save()
                    return self.state.run.status
            if outcome == Outcome.deferred:
                continue
            if outcome in (Outcome.failed, Outcome.blocked):
                continue

    def _finish_empty_or_waiting(self) -> RunStatus:
        assert self.state is not None
        pending = [f for f in self.state.features if f.status == FeatureStatus.pending]
        reasons = [f"{f.id} waits on {','.join(waiting_on(f, self.state))}" for f in pending if waiting_on(f, self.state)]
        self.state.run.status = RunStatus.halted if reasons else RunStatus.finished
        self.state.run.status_reason = "; ".join(reasons) or None
        if self.state.run.status == RunStatus.finished:
            self._clear_current()
        self._save()
        return self.state.run.status

    def _clear_current(self) -> None:
        if self.state is None:
            return
        self.state.run.current_feature = None
        self.state.run.current_step = None
        self.state.run.current_phase = None
        self.state.run.current_agent = None

    def _control_outcome(self) -> Outcome | None:
        control = self.ws.read_control()
        if control == "stop" or self._stop:
            self.ws.clear_control()
            return Outcome.stopped
        if control == "pause":
            self.ws.clear_control()
            return Outcome.paused
        return None

    def _halt_reason(self) -> str | None:
        if self.state is None:
            return None
        failures = [f"{f.id}:{f.block_reason or f.status.value}" for f in self.state.features if f.status in (FeatureStatus.blocked, FeatureStatus.failed)]
        return ", ".join(failures) or None

    def _feature(self, feature_id: str) -> Feature | None:
        if self.state is None:
            return None
        return next((feature for feature in self.state.features if feature.id == feature_id), None)

    def _run_feature(self, feature: Feature) -> Outcome:
        if feature.status == FeatureStatus.pending:
            outcome = self._start_feature(feature)
            if outcome is not None:
                return outcome
        else:
            outcome = self._resume_feature(feature)
            if outcome is not None:
                return outcome
        current = self._feature(feature.id)
        if current is None:
            return Outcome.failed
        for step in feature_steps(self.cfg):
            outcome = self._execute(current, step)
            if outcome != Outcome.ok:
                return outcome
            current = self._feature(feature.id)
            if current is None:
                return Outcome.failed
        return self._converge(current)

    def _start_feature(self, feature: Feature) -> Outcome | None:
        select = Step("select", "Select", AgentName.claude, commit=False)
        outcome = self._execute(feature, select)
        if outcome != Outcome.ok:
            return outcome
        feature = self._feature(feature.id)
        if feature is None:
            return Outcome.failed
        waiting = waiting_on(feature, self.state)
        if waiting:
            return Outcome.deferred
        feature.status = FeatureStatus.in_progress
        feature.started_at = feature.started_at or self.now()
        self._start_branch(feature)
        self._save()
        self._log("feature_started", feature=feature.id)
        return None

    def _start_branch(self, feature: Feature) -> None:
        dirty = gitops.dirty_files(self.ws.repo)
        if self.cfg.git.branch_per_feature and gitops.repos(self.ws.repo) and not dirty:
            feature.branch = gitops.ensure_branch(self.ws.repo, self.cfg.git.branch_prefix + feature.id)
            feature.branch = gitops.current_branch(self.ws.repo)
        elif self.cfg.git.allow_dirty_start:
            feature.branch = gitops.current_branch(self.ws.repo)
        head = gitops.head_sha(self.ws.repo)
        if feature.base_commit is None:
            feature.base_commit = head
        if self.state is not None:
            self.state.run.last_checkpoint = head

    def _resume_feature(self, feature: Feature) -> Outcome | None:
        if not self.cfg.git.branch_per_feature or not feature.branch:
            return None
        branch = gitops.current_branch(self.ws.repo)
        if branch == feature.branch:
            return None
        dirty = gitops.dirty_files(self.ws.repo)
        if dirty:
            return self._pause_unexpected(", ".join(dirty))
        gitops.ensure_branch(self.ws.repo, feature.branch)
        return None

    def _pause_unexpected(self, detail: str) -> Outcome:
        assert self.state is not None
        self.state.run.status = RunStatus.paused
        self.state.run.status_reason = f"unexpected_changes: {detail}"
        self._log("unexpected_changes", feature=self.state.run.current_feature, step=self.state.run.current_step, files=detail)
        self._save()
        return Outcome.paused

    def _converge(self, feature: Feature) -> Outcome:
        start = feature.correction_round
        for round_number in range(start, self.cfg.convergence.max_corrections + 1):
            outcome = self._execute(feature, converge_step(round_number))
            if outcome != Outcome.ok:
                return outcome
            feature = self._feature(feature.id)
            if feature is None:
                return Outcome.failed
            verdict = read_verdict(self.ws.repo, feature.spec_dir or "", round_number)
            validations = read_validations(self.ws.repo, feature.spec_dir or "", round_number)
            if verdict and verdict.approved and all(item.passed for item in validations):
                self._complete(feature, verdict)
                return Outcome.completed
            reason = block_reason(verdict, validations) if verdict else self._missing_verdict_reason(validations)
            if round_number == self.cfg.convergence.max_corrections:
                self._block(feature, reason, verdict.summary if verdict else None)
                return Outcome.blocked
            for step in correction_steps(round_number + 1):
                outcome = self._execute(feature, step)
                if outcome != Outcome.ok:
                    return outcome
            feature.correction_round = round_number + 1
            self._save()
        return Outcome.blocked

    def _missing_verdict_reason(self, validations: list) -> str:
        return block_reason(None, validations)

    def _complete(self, feature: Feature, verdict: Verdict) -> None:
        feature.status = FeatureStatus.completed
        feature.finished_at = self.now()
        self._history(feature, "converge", "feature_completed", verdict.summary)
        self._log("feature_completed", feature=feature.id)
        self._clear_current()
        self._save()
        if self.cfg.git.push:
            try:
                branch = feature.branch or gitops.current_branch(self.ws.repo) or ""
                if not gitops.push(self.ws.repo, self.cfg.git.remote, branch):
                    self._log("push_failed", feature=feature.id)
            except (OSError, GitError) as error:
                self._log("push_failed", feature=feature.id, error=str(error))

    def _block(self, feature: Feature, reason: str, verdict_summary: str | None = None) -> None:
        feature.status = FeatureStatus.blocked
        feature.block_reason = reason
        feature.finished_at = self.now()
        if verdict_summary:
            self._history(feature, "converge", "verdict", verdict_summary)
        self._history(feature, "converge", "feature_blocked", reason)
        self._log("feature_blocked", feature=feature.id, reason=reason)
        self._clear_current()
        self._save()

    def _fail(self, feature: Feature, reason: str) -> None:
        feature.status = FeatureStatus.failed
        feature.block_reason = reason
        feature.finished_at = self.now()
        self._history(feature, self.state.run.current_step if self.state else "", "feature_failed", reason)
        self._log("feature_failed", feature=feature.id, reason=reason)
        if self.state is not None:
            self.state.run.status = RunStatus.halted
            self.state.run.last_error = reason
            self.state.run.pending_recovery = None
        self._save()

    def _execute(self, feature: Feature, step: Step) -> Outcome:
        if step.id in feature.completed_steps:
            return Outcome.ok
        control = self._control_outcome()
        if control is not None:
            return control
        unexpected = self._unexpected_changes(step)
        if unexpected:
            return self._pause_unexpected(unexpected)
        assert self.state is not None
        self.state.run.current_feature = feature.id
        self.state.run.current_step = step.id
        self.state.run.current_phase = step.phase
        self.state.run.current_agent = step.agent
        self.state.run.active_step = step.id
        self._save()
        self._log("step_started", feature=feature.id, step=step.id)
        self._prepare(feature, step)
        recovery = self.state.run.pending_recovery
        valid_recovery = recovery if recovery and recovery.feature_id == feature.id and recovery.step == step.id else None
        streak = valid_recovery.attempt if valid_recovery else 0
        streak_kind = valid_recovery.kind if valid_recovery else None
        while True:
            result = self._attempt(feature, step)
            if result.process.stopped or self._stop:
                self.state.run.status = RunStatus.stopped
                self._save()
                return Outcome.stopped
            if result.ok:
                self._checkpoint(feature, step)
                if feature.status == FeatureStatus.failed:
                    return Outcome.failed
                return Outcome.ok
            kind = result.failure or FailureKind.execution_error
            streak = streak + 1 if streak_kind == kind else 1
            streak_kind = kind
            outcome = self._handle_failure(feature, step, result, streak)
            if outcome is not None:
                return outcome

    def _attempt(self, feature: Feature, step: Step) -> AgentResult:
        assert self.state is not None
        feature.attempts[step.id] = feature.attempts.get(step.id, 0) + 1
        self._save()
        run_dir = self.ws.new_run_dir(feature.id, step.id, feature.attempts[step.id])
        session = feature.sessions.get(step.id) if self.cfg.retry.resume_sessions else None
        self._log("agent_started", feature=feature.id, step=step.id, agent=step.agent.value, attempt=feature.attempts[step.id])
        try:
            result = run_agent(step.agent, self.cfg, self._prompt(feature, step), self.ws, run_dir, session, self._make_stop())
        except OSError as error:
            result = AgentResult(ok=False, failure=FailureKind.execution_error, message=str(error))
        self._last_message = result.message
        self._added_dependencies = []
        if result.session_id:
            feature.sessions[step.id] = result.session_id
        self._log("agent_finished", feature=feature.id, step=step.id, agent=step.agent.value, attempt=feature.attempts[step.id], exit_code=result.process.exit_code, duration_s=result.process.duration_s, failure=result.failure.value if result.failure else None, detail=result.message)
        if result.ok:
            error = self._branch_error(feature, step) or verify(step, self.ws.repo, feature, result, self.cfg)
            if error:
                result = result.model_copy(update={"ok": False, "failure": FailureKind.invalid_output, "message": error})
            elif step.id == "select":
                dependencies = parse_select_dependencies(result.message) or []
                try:
                    add_claude_dependencies(self.state, feature, dependencies)
                    self._added_dependencies = dependencies
                except ValueError as dependency_error:
                    result = result.model_copy(update={"ok": False, "failure": FailureKind.invalid_output, "message": str(dependency_error)})
            if step.id == "specify" and not error:
                feature.spec_dir = read_spec_dir(self.ws.repo)
        self._save()
        return result

    def _branch_error(self, feature: Feature, step: Step) -> str | None:
        if step.id == "select" or not feature.branch:
            return None
        branch = gitops.current_branch(self.ws.repo)
        return f"branch changed during step to {branch}" if branch != feature.branch else None

    def _handle_failure(self, feature: Feature, step: Step, result: AgentResult, streak: int) -> Outcome | None:
        assert self.state is not None
        kind = result.failure or FailureKind.execution_error
        decision = decide(kind, streak, result.reset_at, self.now(), self.cfg.retry)
        if decision.action == "pause":
            self.state.run.status = RunStatus.paused
            self.state.run.status_reason = f"auth: {result.message}"
            self._save()
            return Outcome.paused
        if decision.action == "fail":
            self._fail(feature, result.message)
            return Outcome.failed
        self.state.run.pending_recovery = PendingRecovery(
            feature_id=feature.id,
            step=step.id,
            kind=kind,
            attempt=streak,
            next_retry_at=self.now() + timedelta(seconds=decision.delay_s),
            message=result.message,
        )
        self.state.run.status = RunStatus.waiting
        self.state.run.last_error = result.message
        self._save()
        self._log("waiting", feature=feature.id, step=step.id, delay_s=decision.delay_s)
        outcome = self._wait(decision.delay_s)
        if outcome is not None:
            self.state.run.status = RunStatus(outcome.value)
            self._save()
            return outcome
        self.state.run.status = RunStatus.running
        return None

    def _wait(self, delay_s: float) -> Outcome | None:
        remaining = max(delay_s, 0.0)
        while remaining > 0:
            outcome = self._control_outcome()
            if outcome is not None:
                return outcome
            duration = min(remaining, 1.0)
            self.sleep(duration)
            remaining -= duration
        return None

    def _checkpoint(self, feature: Feature, step: Step) -> None:
        assert self.state is not None
        if step.commit:
            try:
                gitops.commit_all(self.ws.repo, f"orchestrator({feature.id}): {step.id}")
            except GitError as error:
                self._fail(feature, str(error))
                return
        self.state.run.last_checkpoint = gitops.head_sha(self.ws.repo)
        feature.completed_steps.append(step.id)
        self.state.run.active_step = None
        self.state.run.pending_recovery = None
        self.state.run.last_error = None
        self._history(feature, step.id, "step_completed", "ok")
        self._log("step_completed", feature=feature.id, step=step.id)
        self._log("checkpoint", feature=feature.id, step=step.id)
        self._save()
        if step.id == "select":
            for dependency in self._added_dependencies:
                self._log("dependency_added", feature=feature.id, rationale=dependency.get("rationale"), added_by="claude")
            self._save()

    def _unexpected_changes(self, step: Step) -> str | None:
        assert self.state is not None
        checkpoint = self.state.run.last_checkpoint
        head = gitops.head_sha(self.ws.repo)
        dirty = gitops.dirty_files(self.ws.repo)
        if checkpoint and head != checkpoint:
            return "HEAD moved"
        if dirty and self.state.run.active_step != step.id:
            return ", ".join(dirty)
        return None

    def _prepare(self, feature: Feature, step: Step) -> None:
        if step.agent == AgentName.claude and step.id not in ("select", "specify"):
            self._write_feature_json(feature)
        if not feature.spec_dir:
            return
        orchestrator = self.ws.repo / feature.spec_dir / "orchestrator"
        if step.id == "post_analyze":
            orchestrator.mkdir(parents=True, exist_ok=True)
            if feature.base_commit:
                (orchestrator / "diff-post.patch").write_text(gitops.diff(self.ws.repo, feature.base_commit))
        if step.id.startswith("converge."):
            round_number = int(step.id.split(".")[1])
            orchestrator.mkdir(parents=True, exist_ok=True)
            validations = run_validations(self.ws.repo, self.cfg, self.ws.new_run_dir(feature.id, step.id, feature.attempts.get(step.id, 0) + 1))
            (orchestrator / f"validation-{round_number}.json").write_text(json.dumps([item.model_dump(mode="json") for item in validations]))
            if feature.base_commit:
                (orchestrator / f"diff-{round_number}.patch").write_text(gitops.diff(self.ws.repo, feature.base_commit))

    def _write_feature_json(self, feature: Feature) -> None:
        if feature.spec_dir:
            path = self.ws.repo / ".specify" / "feature.json"
            self.ws.atomic_write_json(path, {"feature_directory": feature.spec_dir})

    def _prompt(self, feature: Feature, step: Step) -> str:
        assert self.state is not None
        round_number = int(step.id.split(".")[-1]) if step.id.startswith(("converge.", "correct.handoff.")) else 0
        retry_note = self._retry_note(feature, step)
        if step.agent == AgentName.opencode:
            suffix = f"correction-{round_number}" if step.id.startswith("correct.run.") else "implement"
            handoff = f"{feature.spec_dir}/orchestrator/handoff-{suffix}.md"
            return opencode_prompt(step.id, feature, handoff, feature.spec_dir, round=round_number, retry_note=retry_note)
        return engineering_prompt(
            step.id, feature, self.state, feature.spec_dir, round=round_number,
            cfg=self.cfg, retry_note=retry_note,
        )

    def _retry_note(self, feature: Feature, step: Step) -> str | None:
        assert self.state is not None
        recovery = self.state.run.pending_recovery
        if not recovery or recovery.feature_id != feature.id or recovery.step != step.id:
            return None
        if recovery.kind in (FailureKind.usage_limit, FailureKind.network, FailureKind.auth):
            return None
        return recovery.message

    def _history(self, feature: Feature, step: str, event: str, detail: str) -> None:
        feature.history.append(HistoryEvent(at=self.now(), step=step, event=event, detail=detail))

    def _make_stop(self) -> Callable[[], bool]:
        return lambda: self._stop or self.ws.read_control() == "stop"
