from __future__ import annotations

import signal
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from . import gitops, speckit
from .agents import run_agent
from .backlog import next_feature, waiting_on
from .config import Config
from .failures import decide
from .gitops import GitError
from .models import (
    AgentName,
    AgentResult,
    FailureKind,
    Feature,
    FeatureStatus,
    RunStatus,
    Stage,
    State,
    utcnow,
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
        self.state = State()
        self._stop = False
        self._usage_streak = 0

    def run(self, max_features: int | None = None, run_id: str | None = None) -> RunStatus:
        self.ws.acquire_lock()
        handlers = {}
        try:
            self.state = self.ws.load_state()
            self._start_gate()
            if self.handle_signals:
                handlers = {sig: signal.signal(sig, self._on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
            self.state.run.id = run_id or uuid.uuid4().hex[:8]
            self._set_status(RunStatus.running, None)
            self.state.run.started_at = self.now()
            self._log("run_started", run=self.state.run.id)
            self._loop(max_features)
            self._log("run_ended", status=self.state.run.status.value, reason=self.state.run.reason)
            return self.state.run.status
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            self.ws.release_lock()

    def _on_signal(self, signum: int, frame: object) -> None:
        self._stop = True

    def _start_gate(self) -> None:
        if self._interrupted_stage():
            return
        run = self.state.run
        run.feature = run.stage = run.agent = None
        if self.cfg.git.allow_dirty_start:
            return
        dirty = gitops.dirty_files(self.ws.repo)
        if dirty:
            raise StartRefused("working tree has uncommitted changes: " + ", ".join(dirty))

    def _interrupted_stage(self) -> bool:
        run = self.state.run
        return bool(run.feature and run.stage and run.checkpoint == gitops.head_sha(self.ws.repo))

    def _loop(self, max_features: int | None) -> None:
        completed = 0
        while not self._stopped_by_control():
            feature = next_feature(self.state)
            if feature is None:
                return self._finish()
            self._run_feature(feature)
            if self.state.run.status == RunStatus.stopped:
                return
            if feature.status == FeatureStatus.completed:
                completed += 1
                if max_features is not None and completed >= max_features:
                    return self._set_status(RunStatus.idle, "max features reached")

    def _finish(self) -> None:
        waiting = [f"{f.id} waits on {','.join(waiting_on(f, self.state))}" for f in self.state.features if f.status == FeatureStatus.pending]
        self._clear_current()
        self._set_status(RunStatus.finished, "; ".join(waiting) or None)

    def _run_feature(self, feature: Feature) -> None:
        if not self._enter_branch(feature):
            return
        while not self._stopped_by_control():
            decision = speckit.next_stage(feature, self.ws.repo, self.cfg.convergence.max_corrections)
            self._save()
            if decision == "completed":
                return self._complete(feature)
            if decision == "blocked":
                return self._block(feature, f"open tasks remain after {feature.round} correction rounds")
            if not self._execute(feature, Stage(decision)):
                return

    def _enter_branch(self, feature: Feature) -> bool:
        feature.status = FeatureStatus.in_progress
        repo = self.ws.repo
        while self.cfg.git.branch_per_feature and gitops.repos(repo):
            target = feature.branch or self.cfg.git.branch_prefix + feature.id
            if gitops.current_branch(repo) == target:
                break
            dirty = gitops.dirty_files(repo)
            if not dirty:
                gitops.ensure_branch(repo, target)
                feature.branch = gitops.current_branch(repo)
                break
            if self.cfg.git.allow_dirty_start and not feature.branch:
                break
            if self._pause("unexpected_changes: " + ", ".join(dirty)):
                return False
        feature.branch = feature.branch or gitops.current_branch(repo)
        if self.state.run.feature != feature.id:
            self.state.run.checkpoint = gitops.head_sha(repo)
        self._save()
        return True

    def _execute(self, feature: Feature, stage: Stage) -> bool:
        unexpected = self._unexpected(feature, stage)
        if unexpected:
            return not self._pause("unexpected_changes: " + unexpected) and self._execute(feature, stage)
        agent = speckit.agent_for(stage)
        run = self.state.run
        run.feature, run.stage, run.agent = feature.id, stage, agent
        self._log("stage_started", feature.id, stage, agent=agent.value)
        note = None
        while True:
            if self._wait_for_agent(agent):
                return False
            result = self._call(feature, stage, agent, note)
            if result.process.stopped or self._stop:
                self._set_status(RunStatus.stopped, "stop requested")
                return False
            moved = self._moved(feature)
            if moved:
                if self._pause("unexpected_changes: " + moved):
                    return False
                continue
            if result.ok:
                return self._commit(feature, stage, agent)
            action = self._on_failure(feature, agent, result)
            if action == "stop":
                return False
            note = result.message if result.failure == FailureKind.invalid_output else None

    def _unexpected(self, feature: Feature, stage: Stage) -> str | None:
        run = self.state.run
        if run.checkpoint and gitops.head_sha(self.ws.repo) != run.checkpoint:
            return "HEAD moved"
        dirty = gitops.dirty_files(self.ws.repo)
        resuming = run.feature == feature.id and run.stage == stage
        return ", ".join(dirty) if dirty and not resuming else None

    def _moved(self, feature: Feature) -> str | None:
        if feature.branch and gitops.current_branch(self.ws.repo) != feature.branch:
            return f"branch changed during stage to {gitops.current_branch(self.ws.repo)}"
        if self.state.run.checkpoint and gitops.head_sha(self.ws.repo) != self.state.run.checkpoint:
            return "HEAD moved during stage"
        return None

    def _call(self, feature: Feature, stage: Stage, agent: AgentName, note: str | None) -> AgentResult:
        self._set_status(RunStatus.running, None)
        session = feature.session_id if self.cfg.retry.resume_sessions else None
        message = speckit.instruction(stage, feature, note)
        try:
            result = run_agent(agent, self.cfg, message, self.ws.repo, feature.spec_dir, session, self._should_stop)
        except OSError as error:
            result = AgentResult(ok=False, failure=FailureKind.execution_error, message=str(error))
        feature.session_id = result.session_id or feature.session_id
        process = result.process
        self._log("agent_finished", feature.id, stage, agent=agent.value, exit_code=process.exit_code,
                  duration_s=round(process.duration_s, 1), failure=result.failure, output=result.message[-4000:])
        if result.ok:
            error = self._accept(feature, stage)
            if error:
                result = result.model_copy(update={"ok": False, "failure": FailureKind.invalid_output, "message": error})
        self._save()
        return result

    def _accept(self, feature: Feature, stage: Stage) -> str | None:
        if stage == Stage.specify:
            feature.spec_dir = speckit.read_spec_dir(self.ws.repo)
            if feature.spec_dir and any(f.spec_dir == feature.spec_dir for f in self.state.features if f is not feature):
                taken, feature.spec_dir = feature.spec_dir, None
                return f"{taken} already belongs to another feature; specify did not create a new one"
        return speckit.artifact_error(stage, self.ws.repo, feature)

    def _on_failure(self, feature: Feature, agent: AgentName, result: AgentResult) -> str:
        kind = result.failure or FailureKind.execution_error
        if kind == FailureKind.usage_limit:
            self._usage_streak += 1
            delay = decide(kind, self._usage_streak, result.reset_at, self.now(), self.cfg.retry).delay_s
            self.state.unavailable[agent] = self.now() + timedelta(seconds=delay)
            self._log("agent_unavailable", feature.id, agent=agent.value, until=self.state.unavailable[agent].isoformat())
            return "retry"
        feature.attempts += 1
        feature.last_error = result.message
        decision = decide(kind, feature.attempts, result.reset_at, self.now(), self.cfg.retry)
        if decision.action == "pause":
            feature.attempts -= 1
            return "stop" if self._pause(f"auth: {result.message}") else "retry"
        if decision.action == "fail":
            self._block(feature, result.message)
            return "stop"
        self._set_status(RunStatus.waiting, f"{kind.value}: retry {feature.attempts}")
        return "stop" if self._wait(decision.delay_s) else "retry"

    def _wait_for_agent(self, agent: AgentName) -> bool:
        until = self.state.unavailable.get(agent)
        if until is None or until <= self.now():
            return False
        self._set_status(RunStatus.waiting, f"{agent.value} unavailable until {until.isoformat()}")
        return self._wait((until - self.now()).total_seconds())

    def _wait(self, delay_s: float) -> bool:
        remaining = max(delay_s, 0.0)
        while remaining > 0:
            if self._stopped_by_control():
                return True
            step = min(remaining, 1.0)
            self.sleep(step)
            remaining -= step
        return False

    def _stopped_by_control(self) -> bool:
        control = self.ws.read_control()
        if self._stop or control == "stop":
            self.ws.clear_control()
            self._set_status(RunStatus.stopped, "stop requested")
            return True
        if control == "pause":
            self.ws.clear_control()
            return self._pause("pause requested")
        return False

    def _pause(self, reason: str) -> bool:
        previous = self.state.run.status
        self._set_status(RunStatus.paused, reason)
        self._log("paused", self.state.run.feature, reason=reason)
        while True:
            control = self.ws.read_control()
            if self._stop or control == "stop":
                self.ws.clear_control()
                self._set_status(RunStatus.stopped, "stop requested")
                return True
            if control == "resume":
                self.ws.clear_control()
                self._set_status(previous if previous != RunStatus.paused else RunStatus.running, None)
                self._log("resumed", self.state.run.feature)
                return False
            self.sleep(1.0)

    def _commit(self, feature: Feature, stage: Stage, agent: AgentName) -> bool:
        try:
            gitops.commit_all(self.ws.repo, f"orchestrator({feature.id}): {stage.value}")
        except GitError as error:
            self._block(feature, str(error))
            return False
        feature.done.append(stage)
        feature.attempts, feature.session_id, feature.last_error = 0, None, None
        self.state.unavailable.pop(agent, None)
        self.state.run.checkpoint = gitops.head_sha(self.ws.repo)
        self.state.run.stage = None
        self._usage_streak = 0
        self._log("stage_completed", feature.id, stage)
        self._save()
        return True

    def _complete(self, feature: Feature) -> None:
        feature.status = FeatureStatus.completed
        feature.last_error = None
        self._log("feature_completed", feature.id)
        self._clear_current()
        self._save()
        if self.cfg.git.push:
            try:
                if not gitops.push(self.ws.repo, self.cfg.git.remote, feature.branch or ""):
                    self._log("push_failed", feature.id)
            except (OSError, GitError) as error:
                self._log("push_failed", feature.id, error=str(error))

    def _block(self, feature: Feature, reason: str) -> None:
        feature.status = FeatureStatus.blocked
        feature.last_error = reason
        self._log("feature_blocked", feature.id, reason=reason)
        self._clear_current()
        self._set_status(RunStatus.running, None)

    def _clear_current(self) -> None:
        run = self.state.run
        run.feature = run.stage = run.agent = None

    def _set_status(self, status: RunStatus, reason: str | None) -> None:
        self.state.run.status = status
        self.state.run.reason = reason
        self._save()

    def _save(self) -> None:
        self.state.run.updated_at = self.now()
        self.ws.save_state(self.state)

    def _log(self, event: str, feature: str | None = None, stage: Stage | None = None, **fields: object) -> None:
        self.ws.log_event(event, feature=feature, step=stage.value if stage else None, **fields)

    def _should_stop(self) -> bool:
        return self._stop or self.ws.read_control() == "stop"
