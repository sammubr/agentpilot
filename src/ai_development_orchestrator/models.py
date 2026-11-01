from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum, StrEnum

from pydantic import BaseModel, Field, field_validator


class Outcome(StrEnum):
    ok = "ok"
    deferred = "deferred"
    completed = "completed"
    blocked = "blocked"
    failed = "failed"
    paused = "paused"
    stopped = "stopped"


def utcnow() -> datetime:
    return datetime.now(UTC)


class FeatureStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    blocked = "blocked"
    failed = "failed"


class RunStatus(str, Enum):
    idle = "idle"
    running = "running"
    waiting = "waiting"
    paused = "paused"
    stopped = "stopped"
    finished = "finished"
    halted = "halted"


class FailureKind(str, Enum):
    usage_limit = "usage_limit"
    auth = "auth"
    network = "network"
    timeout = "timeout"
    invalid_output = "invalid_output"
    execution_error = "execution_error"


class AgentName(str, Enum):
    claude = "claude"
    opencode = "opencode"


class Dependency(BaseModel):
    id: str
    rationale: str | None = None
    added_by: str

    @field_validator("added_by")
    @classmethod
    def validate_added_by(cls, v: str) -> str:
        if v not in ("operator", "claude"):
            raise ValueError("added_by must be operator or claude")
        return v


class HistoryEvent(BaseModel):
    at: datetime
    step: str | None = None
    event: str
    detail: str


class Feature(BaseModel):
    id: str
    title: str
    description: str
    dependencies: list[Dependency] = Field(default_factory=list)
    status: FeatureStatus = FeatureStatus.pending
    spec_dir: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    attempts: dict[str, int] = Field(default_factory=dict)
    sessions: dict[str, str] = Field(default_factory=dict)
    correction_round: int = 0
    block_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    history: list[HistoryEvent] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", v):
            raise ValueError("invalid feature id")
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v:
            raise ValueError("title non-empty")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if not v:
            raise ValueError("description non-empty")
        return v


class PendingRecovery(BaseModel):
    feature_id: str
    step: str
    kind: FailureKind
    attempt: int
    next_retry_at: datetime
    message: str


class RunState(BaseModel):
    status: RunStatus = RunStatus.idle
    current_feature: str | None = None
    current_step: str | None = None
    current_phase: str | None = None
    current_agent: AgentName | None = None
    active_step: str | None = None
    last_checkpoint: str | None = None
    pending_recovery: PendingRecovery | None = None
    last_error: str | None = None
    status_reason: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None


class State(BaseModel):
    version: int = 1
    features: list[Feature] = Field(default_factory=list)
    run: RunState = Field(default_factory=RunState)


class Finding(BaseModel):
    severity: str
    description: str
    location: str | None = None


class AcceptanceCriterionResult(BaseModel):
    criterion: str
    met: bool
    evidence: str


class Verdict(BaseModel):
    approved: bool
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterionResult] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def validate_findings(cls, v: list[Finding]) -> list[Finding]:
        for f in v:
            if f.severity not in ("critical", "major", "minor"):
                raise ValueError("invalid severity")
        return v


class ValidationResult(BaseModel):
    command: str
    exit_code: int | None
    passed: bool
    output_tail: str
    duration_s: float


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
    process: ProcessResult
