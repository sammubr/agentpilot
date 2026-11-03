from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Stage(StrEnum):
    specify = "specify"
    clarify = "clarify"
    plan = "plan"
    tasks = "tasks"
    analyze = "analyze"
    implement = "implement"
    converge = "converge"


PLANNING = [Stage.specify, Stage.clarify, Stage.plan, Stage.tasks, Stage.analyze]


class FeatureStatus(StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    blocked = "blocked"


class RunStatus(StrEnum):
    idle = "idle"
    running = "running"
    waiting = "waiting"
    paused = "paused"
    stopped = "stopped"
    finished = "finished"


class FailureKind(StrEnum):
    usage_limit = "usage_limit"
    auth = "auth"
    network = "network"
    timeout = "timeout"
    invalid_output = "invalid_output"
    execution_error = "execution_error"


class AgentName(StrEnum):
    claude = "claude"
    opencode = "opencode"


class Feature(BaseModel):
    id: str
    title: str
    description: str
    status: FeatureStatus = FeatureStatus.pending
    spec_dir: str | None = None
    branch: str | None = None
    branch_base: str | None = None
    done: list[Stage] = Field(default_factory=list)
    round: int = 0
    attempts: int = 0
    session_id: str | None = None
    last_error: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", v):
            raise ValueError(f"invalid feature id: {v!r}")
        return v

    @field_validator("title", "description")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title and description must be non-empty")
        return v


class RunState(BaseModel):
    id: str | None = None
    status: RunStatus = RunStatus.idle
    reason: str | None = None
    feature: str | None = None
    stage: Stage | None = None
    agent: AgentName | None = None
    checkpoint: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None


class State(BaseModel):
    version: int = 2
    features: list[Feature] = Field(default_factory=list)
    run: RunState = Field(default_factory=RunState)
    unavailable: dict[AgentName, datetime] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def upgrade(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("version", 1) == 1 and "features" in data:
            return migrate_v1(data)
        return data


def migrate_v1(data: dict) -> dict:
    return {"version": 2, "features": [_migrate_feature(item) for item in data.get("features", [])]}


def _migrate_feature(item: dict) -> dict:
    status = {"completed": "completed", "blocked": "blocked", "failed": "blocked"}.get(item.get("status"), "pending")
    steps = item.get("completed_steps", [])
    done = [stage for stage in PLANNING if stage.value in steps]
    if any(step == "implement.run" or step.startswith("correct.run.") for step in steps):
        done.append(Stage.implement)
    return {
        "id": item["id"], "title": item["title"], "description": item["description"],
        "status": status, "spec_dir": item.get("spec_dir"), "branch": item.get("branch"), "done": done,
    }


class ProcessResult(BaseModel):
    exit_code: int | None = None
    timed_out: bool = False
    stopped: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0


class AgentResult(BaseModel):
    ok: bool
    failure: FailureKind | None = None
    message: str = ""
    session_id: str | None = None
    reset_at: datetime | None = None
    process: ProcessResult = Field(default_factory=ProcessResult)
