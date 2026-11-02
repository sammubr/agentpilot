from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from . import gitops
from .agents import AgentName
from .config import Config
from .gitops import GitError
from .models import AgentResult, Feature, ValidationResult, Verdict
from .process import run_process
from .prompts import VALIDATION_COMMANDS_FILE, missing_sections


@dataclass(frozen=True)
class Step:
    id: str
    phase: str
    agent: AgentName
    commit: bool = True


def feature_steps(cfg: Config) -> list[Step]:
    steps = [Step("select", "Select", AgentName.claude, False)]
    steps.extend(Step(step, phase, AgentName.claude) for step, phase in (
        ("specify", "Specify"), ("clarify", "Clarify"), ("plan", "Plan"),
        ("tasks", "Tasks"), ("analyze", "Analyse"),
    ))
    steps.extend(Step(phase.id, phase.id, AgentName.claude) for phase in cfg.phases)
    steps.extend([
        Step("implement.handoff", "Implement", AgentName.claude),
        Step("implement.run", "Implement", AgentName.opencode),
        Step("post_analyze", "Post-implementation Analyse", AgentName.claude),
    ])
    return steps


def converge_step(r: int) -> Step:
    return Step(f"converge.{r}", "Converge", AgentName.claude)


def correction_steps(r: int) -> list[Step]:
    return [Step(f"correct.handoff.{r}", "Converge", AgentName.claude), Step(f"correct.run.{r}", "Implement", AgentName.opencode)]


def phase_of(step_id: str) -> str:
    phases = {
        "select": "Select", "specify": "Specify", "clarify": "Clarify", "plan": "Plan",
        "tasks": "Tasks", "analyze": "Analyse", "implement.handoff": "Implement",
        "implement.run": "Implement", "post_analyze": "Post-implementation Analyse",
    }
    if step_id.startswith(("converge", "correct")):
        return "Converge"
    return phases.get(step_id, step_id)


def read_spec_dir(repo: Path) -> str | None:
    try:
        data = json.loads((repo / ".specify" / "feature.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("feature_directory"), str):
        return None
    spec_dir = Path(data["feature_directory"])
    if not data["feature_directory"]:
        return None
    if spec_dir.is_absolute():
        try:
            spec_dir = spec_dir.resolve().relative_to(repo.resolve())
        except (OSError, ValueError):
            return None
    if ".." in spec_dir.parts:
        return None
    return spec_dir.as_posix()


def parse_select_dependencies(message: str) -> list[dict] | None:
    decoder = json.JSONDecoder()
    for index in range(len(message) - 1, -1, -1):
        if message[index] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(message, index)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("dependencies"), list):
            return obj["dependencies"]
    return None


def read_verdict(repo: Path, spec_dir: str, r: int) -> Verdict | None:
    data = _read_json(repo / spec_dir / "orchestrator" / f"verdict-{r}.json")
    if data is None:
        return None
    try:
        return Verdict.model_validate(data)
    except ValidationError:
        return None


def read_validations(repo: Path, spec_dir: str, r: int) -> list[ValidationResult]:
    data = _read_json(repo / spec_dir / "orchestrator" / f"validation-{r}.json")
    if not isinstance(data, list):
        return []
    results = []
    for item in data:
        try:
            results.append(ValidationResult.model_validate(item))
        except ValidationError:
            continue
    return results


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def run_validations(repo: Path, commands: list[str], timeout_s: int, run_dir: Path) -> list[ValidationResult]:
    results = []
    for index, command in enumerate(commands):
        results.append(_run_validation(repo, command, timeout_s, run_dir / str(index)))
    return results


def _run_validation(repo: Path, command: str, timeout_s: int, run_dir: Path) -> ValidationResult:
    try:
        result = run_process(["bash", "-c", command], cwd=repo, timeout_s=timeout_s, run_dir=run_dir)
        output = (result.stdout + "\n" + result.stderr)[-4000:]
        return ValidationResult(command=command, exit_code=result.exit_code, passed=result.exit_code == 0 and not result.timed_out, output_tail=output, duration_s=result.duration_s)
    except OSError as error:
        return ValidationResult(command=command, exit_code=None, passed=False, output_tail=str(error), duration_s=0.0)


def block_reason(verdict: Verdict | None, validations: list[ValidationResult]) -> str:
    reason = verdict.summary if verdict else "verdict missing or not approved"
    if verdict:
        findings = [
            f"{item.severity}: {item.description} @ {item.location or 'unspecified'}"
            for item in verdict.findings
            if item.severity in ("critical", "major")
        ]
        if findings:
            reason += " | findings: " + "; ".join(findings)
    failed = [
        f"{item.command} (exit {item.exit_code if item.exit_code is not None else 'unknown'})"
        for item in validations
        if not item.passed
    ]
    if failed:
        reason += " | failed validations: " + "; ".join(failed)
    return reason


def verify(step: Step, repo: Path, feature: Feature, result: AgentResult, cfg: Config | None = None) -> str | None:
    step_id = step.id
    if step_id == "select":
        return _verify_select(result.message)
    if step_id == "specify":
        return _verify_specify(repo, feature)
    if step_id in {"clarify", "plan", "tasks", "analyze", "post_analyze"}:
        return _verify_artifact(step_id, repo, feature)
    if step_id == "implement.handoff":
        return _verify_handoff(repo, feature, "handoff-implement.md", False) or _verify_validation_commands(repo, feature, cfg)
    if step_id == "implement.run" or step_id.startswith("correct.run."):
        return _verify_changes(repo, feature)
    if step_id.startswith("converge."):
        return _verify_verdict(repo, feature, step_id)
    if step_id.startswith("correct.handoff."):
        return _verify_correction(repo, feature, step_id)
    return _verify_extra(step.id, repo, feature, cfg)


def _verify_extra(step_id: str, repo: Path, feature: Feature, cfg: Config | None) -> str | None:
    if cfg is None:
        return None
    phase = next((item for item in cfg.phases if item.id == step_id), None)
    if phase is None:
        return None
    for expected in phase.expect:
        paths = [repo / feature.spec_dir / expected, repo / expected] if feature.spec_dir else [repo / expected]
        if not any(path.exists() for path in paths):
            return f"missing {expected}"
    return None


def _verify_select(message: str) -> str | None:
    dependencies = parse_select_dependencies(message)
    if dependencies is None:
        return "no json"
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
            return "invalid dependencies"
        if not isinstance(dependency.get("rationale"), str) or not dependency["rationale"].strip():
            return "invalid dependency rationale"
    return None


def _verify_specify(repo: Path, feature: Feature) -> str | None:
    spec_dir = read_spec_dir(repo)
    if not spec_dir:
        return "missing file: .specify/feature.json or feature_directory"
    if not (repo / spec_dir / "spec.md").is_file():
        return f"missing file: {spec_dir}/spec.md"
    feature.spec_dir = spec_dir
    return None


def _verify_artifact(step_id: str, repo: Path, feature: Feature) -> str | None:
    paths = {
        "clarify": "spec.md", "plan": "plan.md", "tasks": "tasks.md",
        "analyze": "orchestrator/analysis.md", "post_analyze": "orchestrator/post-analysis.md",
    }
    path = paths[step_id]
    if not feature.spec_dir:
        return "missing feature directory"
    artifact = f"{feature.spec_dir}/{path}"
    return None if (repo / artifact).is_file() else f"missing file: {artifact}"


def _verify_handoff(repo: Path, feature: Feature, filename: str, correction: bool) -> str | None:
    if not feature.spec_dir:
        return "missing handoff"
    path = repo / feature.spec_dir / "orchestrator" / filename
    try:
        text = path.read_text()
    except OSError:
        return f"missing file: {feature.spec_dir}/orchestrator/{filename}"
    sections = missing_sections(text, correction=correction)
    return "missing sections: " + ", ".join(sections) if sections else None


def _verify_validation_commands(repo: Path, feature: Feature, cfg: Config | None) -> str | None:
    if cfg and cfg.validation.commands:
        return None
    relative = f"{feature.spec_dir}/orchestrator/{VALIDATION_COMMANDS_FILE}"
    data = _read_json(repo / relative)
    if data is None:
        return f"missing file: {relative}"
    if not isinstance(data, list) or not all(isinstance(item, str) and item.strip() for item in data):
        return f"invalid {relative}: expected a JSON list of shell commands"
    feature.validation_commands = data
    return None


def _verify_changes(repo: Path, feature: Feature) -> str | None:
    try:
        changed = gitops.dirty_files(repo)
    except (OSError, GitError):
        changed = []
    changed = [path for path in changed if _is_implementation_file(path, feature.spec_dir)]
    return None if changed else "implementation produced no changes"


def _is_implementation_file(path: str, spec_dir: str | None) -> bool:
    if path.startswith(".orchestrator/"):
        return False
    return not (spec_dir and (path == spec_dir or path.startswith(spec_dir.rstrip("/") + "/")))


def _verify_verdict(repo: Path, feature: Feature, step_id: str) -> str | None:
    try:
        round_number = int(step_id.split(".")[1])
    except (ValueError, IndexError):
        return "missing verdict"
    if not feature.spec_dir:
        return "missing feature directory"
    path = repo / feature.spec_dir / "orchestrator" / f"verdict-{round_number}.json"
    if not path.is_file():
        return f"missing file: {feature.spec_dir}/orchestrator/verdict-{round_number}.json"
    error = _verdict_error(path)
    return f"verdict invalid: {error}" if error else None


def _verdict_error(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return str(error)
    try:
        Verdict.model_validate(data)
    except ValidationError as error:
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
        )
    return None


def _verify_correction(repo: Path, feature: Feature, step_id: str) -> str | None:
    try:
        round_number = int(step_id.split(".")[2])
    except (ValueError, IndexError):
        return "missing handoff"
    return _verify_handoff(repo, feature, f"handoff-correction-{round_number}.md", True)
