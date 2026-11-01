from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_TOML = """[agents.claude]
command = ["claude"]
model = ""
timeout_s = 3600
permission_mode = "dontAsk"
allowed_tools = [
  "Read", "Glob", "Grep", "Skill", "TodoWrite",
  "Write(specs/**)", "Edit(specs/**)",
  "Write(.specify/feature.json)", "Edit(.specify/feature.json)",
  "Bash(.specify/scripts/bash/*)",
  "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)",
]
disallowed_tools = [
  "Bash(git push*)", "Bash(git commit*)", "Bash(git reset*)", "Bash(git checkout*)",
  "Bash(git clean*)", "Bash(git switch*)", "Bash(git branch*)", "Bash(rm *)", "Bash(sudo *)",
]
extra_args = []

[agents.opencode]
command = ["opencode"]
model = ""
timeout_s = 5400
extra_args = []

[agents.opencode.permission]
edit = "allow"
webfetch = "deny"
external_directory = "deny"
bash = { "*" = "allow", "rm -rf *" = "deny", "rm -r *" = "deny", "rm -fr *" = "deny", "sudo *" = "deny", "git push*" = "deny", "git commit*" = "deny", "git reset*" = "deny", "git checkout*" = "deny", "git switch*" = "deny", "git clean*" = "deny", "git rebase*" = "deny", "git merge*" = "deny", "git branch -D*" = "deny", "git tag*" = "deny", "npm publish*" = "deny", "uv publish*" = "deny", "twine *" = "deny", "docker push*" = "deny", "kubectl *" = "deny", "helm *" = "deny", "terraform *" = "deny", "ssh *" = "deny", "scp *" = "deny", "chown *" = "deny", "mkfs*" = "deny", "dd *" = "deny" }

[retry]
initial_delay_s = 30
multiplier = 2.0
max_delay_s = 1800
max_attempts_network = 10
max_attempts_execution = 3
usage_limit_initial_delay_s = 300
usage_limit_max_wait_s = 21600
usage_limit_margin_s = 60
resume_sessions = true

[convergence]
max_corrections = 3

[validation]
commands = []
timeout_s = 1800

[git]
allow_dirty_start = false
branch_per_feature = true
branch_prefix = "orchestrator/"
push = false
remote = "origin"

[safety]
allow_unrestricted = false
"""


DEFAULT_OPENCODE_PERMISSION = {
    "edit": "allow",
    "webfetch": "deny",
    "external_directory": "deny",
    "bash": {
        "*": "allow",
        "rm -rf *": "deny",
        "rm -r *": "deny",
        "rm -fr *": "deny",
        "sudo *": "deny",
        "git push*": "deny",
        "git commit*": "deny",
        "git reset*": "deny",
        "git checkout*": "deny",
        "git switch*": "deny",
        "git clean*": "deny",
        "git rebase*": "deny",
        "git merge*": "deny",
        "git branch -D*": "deny",
        "git tag*": "deny",
        "npm publish*": "deny",
        "uv publish*": "deny",
        "twine *": "deny",
        "docker push*": "deny",
        "kubectl *": "deny",
        "helm *": "deny",
        "terraform *": "deny",
        "ssh *": "deny",
        "scp *": "deny",
        "chown *": "deny",
        "mkfs*": "deny",
        "dd *": "deny",
    },
}


class ClaudeConfig(BaseModel):
    command: list[str] = ["claude"]
    model: str = ""
    timeout_s: int = 3600
    permission_mode: str = "dontAsk"
    allowed_tools: list[str] = [
        "Read",
        "Glob",
        "Grep",
        "Skill",
        "TodoWrite",
        "Write(specs/**)",
        "Edit(specs/**)",
        "Write(.specify/feature.json)",
        "Edit(.specify/feature.json)",
        "Bash(.specify/scripts/bash/*)",
        "Bash(git status*)",
        "Bash(git diff*)",
        "Bash(git log*)",
        "Bash(git show*)",
    ]
    disallowed_tools: list[str] = [
        "Bash(git push*)",
        "Bash(git commit*)",
        "Bash(git reset*)",
        "Bash(git checkout*)",
        "Bash(git clean*)",
        "Bash(git switch*)",
        "Bash(git branch*)",
        "Bash(rm *)",
        "Bash(sudo *)",
    ]
    extra_args: list[str] = []


class OpencodeConfig(BaseModel):
    command: list[str] = ["opencode"]
    model: str = ""
    timeout_s: int = 5400
    extra_args: list[str] = []
    permission: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_OPENCODE_PERMISSION))


class RetryConfig(BaseModel):
    initial_delay_s: float = 30
    multiplier: float = 2.0
    max_delay_s: float = 1800
    max_attempts_network: int = 10
    max_attempts_execution: int = 3
    usage_limit_initial_delay_s: float = 300
    usage_limit_max_wait_s: float = 21600
    usage_limit_margin_s: float = 60
    resume_sessions: bool = True

    @field_validator("initial_delay_s", "usage_limit_initial_delay_s", "usage_limit_max_wait_s", "max_delay_s")
    @classmethod
    def validate_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("invalid retry")
        return v

    @field_validator("usage_limit_margin_s")
    @classmethod
    def validate_margin(cls, v: float) -> float:
        if v < 0:
            raise ValueError("invalid retry")
        return v


class ConvergenceConfig(BaseModel):
    max_corrections: int = 3


class ValidationConfig(BaseModel):
    commands: list[str] = []
    timeout_s: int = 1800


class GitConfig(BaseModel):
    allow_dirty_start: bool = False
    branch_per_feature: bool = True
    branch_prefix: str = "orchestrator/"
    push: bool = False
    remote: str = "origin"


class SafetyConfig(BaseModel):
    allow_unrestricted: bool = False


class PhaseConfig(BaseModel):
    id: str
    prompt: str
    expect: list[str] = []


RESERVED_PHASE_IDS = {"implement", "converge", "correct"}


class Config(BaseModel):
    agents: dict[str, Any]
    retry: RetryConfig = Field(default_factory=RetryConfig)
    convergence: ConvergenceConfig = Field(default_factory=ConvergenceConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    phases: list[PhaseConfig] = []

    @field_validator("phases")
    @classmethod
    def validate_phases(cls, v: list[PhaseConfig]) -> list[PhaseConfig]:
        allowed = {"specify", "clarify", "plan", "tasks", "analyze", "post_analyze"}
        for p in v:
            if p.id in RESERVED_PHASE_IDS:
                raise ValueError("reserved phase id")
            if p.id.startswith("implement") or p.id.startswith("converge") or p.id.startswith("correct"):
                raise ValueError("reserved phase id")
            if p.id in allowed:
                continue
            if not re.match(r"^[a-z][a-z0-9_]*$", p.id):
                raise ValueError("invalid phase id")
        return v

    @field_validator("retry")
    @classmethod
    def validate_retry(cls, v: RetryConfig) -> RetryConfig:
        if v.initial_delay_s <= 0 or v.multiplier < 1:
            raise ValueError("invalid retry")
        return v

    @field_validator("convergence")
    @classmethod
    def validate_convergence(cls, v: ConvergenceConfig) -> ConvergenceConfig:
        if v.max_corrections < 0:
            raise ValueError("invalid convergence")
        return v


def load_config(path: Path) -> Config:
    raw = path.read_text()
    data = tomllib.loads(raw)
    cfg = build_config(data)
    validate_config(cfg, data)
    return cfg


def build_config(data: dict[str, Any]) -> Config:
    claude = ClaudeConfig(**data.get("agents", {}).get("claude", {}))
    opencode_raw = dict(data.get("agents", {}).get("opencode", {}))
    perm_raw = opencode_raw.pop("permission", None)
    opencode = OpencodeConfig(**opencode_raw)
    if perm_raw is not None:
        if isinstance(perm_raw, dict):
            # merge into default
            perm = dict(DEFAULT_OPENCODE_PERMISSION)
            for k, v in perm_raw.items():
                if k == "bash" and isinstance(v, dict):
                    bash = dict(perm.get("bash", {}))
                    bash.update(v)
                    perm["bash"] = bash
                else:
                    perm[k] = v
            opencode.permission = perm
        elif isinstance(perm_raw, str):
            perm = dict(DEFAULT_OPENCODE_PERMISSION)
            perm["edit"] = perm_raw if perm_raw in ("allow", "deny", "ask") else perm.get("edit", "allow")
            opencode.permission = perm
        else:
            opencode.permission = dict(DEFAULT_OPENCODE_PERMISSION)
    else:
        opencode.permission = dict(DEFAULT_OPENCODE_PERMISSION)
    agents = {"claude": claude, "opencode": opencode}
    d = dict(data)
    d["agents"] = agents
    phases = []
    for p in d.get("phases", []) or []:
        if isinstance(p, dict):
            phases.append(PhaseConfig(**p))
    d["phases"] = phases
    return Config(**d)


def validate_config(cfg: Config, raw: dict[str, Any]) -> None:
    if cfg.safety.allow_unrestricted:
        return
    # Claude checks
    if cfg.agents["claude"].permission_mode == "bypassPermissions":
        raise ValueError("unrestricted not allowed")
    for a in cfg.agents["claude"].extra_args:
        if a == "--dangerously-skip-permissions":
            raise ValueError("unrestricted not allowed")
    # OpenCode checks
    perm = cfg.agents["opencode"].permission
    if not isinstance(perm, dict):
        raise TypeError("unrestricted not allowed")
    bash = perm.get("bash")
    if bash == "allow":
        raise ValueError("unrestricted not allowed")
    if not isinstance(bash, dict):
        raise TypeError("unrestricted not allowed")
    if not any(v == "deny" for v in bash.values()):
        raise ValueError("unrestricted not allowed")
