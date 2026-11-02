from __future__ import annotations

import json
import re
from pathlib import Path

from .models import PLANNING, AgentName, Feature, Stage

UNATTENDED = "Unattended run: "
MESSAGES = {
    Stage.clarify: "/speckit-clarify " + UNATTENDED + "answer each question yourself with the recommended option.",
    Stage.plan: "/speckit-plan",
    Stage.tasks: "/speckit-tasks",
    Stage.analyze: "/speckit-analyze " + UNATTENDED + "after the report, apply fixes for all findings to the artifacts.",
    Stage.converge: "/speckit-converge",
}
IMPLEMENT = (
    "Run the Spec Kit command /speckit.implement (defined in .opencode/commands/speckit.implement.md) "
    "for the feature in {spec_dir}. Do not commit, push or switch branches."
)
ARTIFACTS = {Stage.specify: "spec.md", Stage.clarify: "spec.md", Stage.plan: "plan.md"}
OPEN_TASK = re.compile(r"^\s*- \[ \]", re.MULTILINE)


def instruction(stage: Stage, feature: Feature, retry_note: str | None = None) -> str:
    if stage == Stage.specify:
        text = f"/speckit-specify {feature.title}: {feature.description}\nDo not create a Git branch."
    elif stage == Stage.implement:
        text = IMPLEMENT.format(spec_dir=feature.spec_dir)
    else:
        text = MESSAGES[stage]
    return f"{text}\nPrevious attempt rejected: {retry_note}" if retry_note else text


def agent_for(stage: Stage) -> AgentName:
    return AgentName.opencode if stage == Stage.implement else AgentName.claude


def read_spec_dir(repo: Path) -> str | None:
    try:
        value = json.loads((repo / ".specify" / "feature.json").read_text()).get("feature_directory")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(repo.resolve())
        except (OSError, ValueError):
            return None
    return None if ".." in path.parts else path.as_posix()


def open_tasks(repo: Path, spec_dir: str) -> int:
    try:
        return len(OPEN_TASK.findall((repo / spec_dir / "tasks.md").read_text()))
    except OSError:
        return 0


def artifact_error(stage: Stage, repo: Path, feature: Feature) -> str | None:
    if not feature.spec_dir:
        return "spec directory unknown: .specify/feature.json does not point to a feature directory"
    name = ARTIFACTS.get(stage, "tasks.md")
    if not (repo / feature.spec_dir / name).is_file():
        return f"missing {feature.spec_dir}/{name} after {stage.value}"
    return None


def next_stage(feature: Feature, repo: Path, max_corrections: int) -> Stage | str:
    _drop_invalid(feature, repo)
    for stage in [*PLANNING, Stage.implement, Stage.converge]:
        if stage not in feature.done:
            return stage
    if open_tasks(repo, feature.spec_dir or "") == 0:
        return "completed"
    if feature.round >= max_corrections:
        return "blocked"
    feature.round += 1
    feature.done = [stage for stage in feature.done if stage in PLANNING]
    return Stage.implement


def _drop_invalid(feature: Feature, repo: Path) -> None:
    order = [*PLANNING, Stage.implement, Stage.converge]
    for stage in (Stage.specify, Stage.plan, Stage.tasks):
        if stage in feature.done and artifact_error(stage, repo, feature):
            cut = order.index(stage)
            feature.done = [item for item in feature.done if order.index(item) < cut]
            return
