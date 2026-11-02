from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

[git]
allow_dirty_start = false
branch_per_feature = true
branch_prefix = "orchestrator/"
push = false
remote = "origin"

[safety]
allow_unrestricted = false
"""

_DEFAULTS = tomllib.loads(DEFAULT_CONFIG_TOML)
DEFAULT_OPENCODE_PERMISSION: dict[str, Any] = _DEFAULTS["agents"]["opencode"]["permission"]
_CLAUDE = _DEFAULTS["agents"]["claude"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaudeConfig(Section):
    command: list[str] = ["claude"]
    model: str = ""
    timeout_s: int = 3600
    permission_mode: str = "dontAsk"
    allowed_tools: list[str] = Field(default_factory=lambda: list(_CLAUDE["allowed_tools"]))
    disallowed_tools: list[str] = Field(default_factory=lambda: list(_CLAUDE["disallowed_tools"]))
    extra_args: list[str] = []


class OpencodeConfig(Section):
    command: list[str] = ["opencode"]
    model: str = ""
    timeout_s: int = 5400
    extra_args: list[str] = []
    permission: dict[str, Any] = Field(default_factory=lambda: _merge_permission({}))


class RetryConfig(Section):
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
            raise ValueError("retry delays must be positive")
        return v

    @field_validator("usage_limit_margin_s")
    @classmethod
    def validate_margin(cls, v: float) -> float:
        if v < 0:
            raise ValueError("usage_limit_margin_s must not be negative")
        return v

    @field_validator("multiplier")
    @classmethod
    def validate_multiplier(cls, v: float) -> float:
        if v < 1:
            raise ValueError("multiplier must be at least 1")
        return v


class ConvergenceConfig(Section):
    max_corrections: int = Field(default=3, ge=0)


class GitConfig(Section):
    allow_dirty_start: bool = False
    branch_per_feature: bool = True
    branch_prefix: str = "orchestrator/"
    push: bool = False
    remote: str = "origin"


class SafetyConfig(Section):
    allow_unrestricted: bool = False


class Config(Section):
    agents: dict[str, Any]
    retry: RetryConfig = Field(default_factory=RetryConfig)
    convergence: ConvergenceConfig = Field(default_factory=ConvergenceConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


def load_config(path: Path) -> Config:
    data = tomllib.loads(path.read_text())
    cfg = build_config(data)
    validate_config(cfg)
    return cfg


def build_config(data: dict[str, Any]) -> Config:
    removed = [key for key in ("validation", "phases") if key in data]
    if removed:
        raise ValueError(f"unsupported config section: {', '.join(removed)} (removed; delete it from config.toml)")
    agents = data.get("agents", {})
    opencode = dict(agents.get("opencode", {}))
    opencode["permission"] = _merge_permission(opencode.get("permission", {}))
    claude = ClaudeConfig(**agents.get("claude", {}))
    return Config(**{**data, "agents": {"claude": claude, "opencode": OpencodeConfig(**opencode)}})


def _merge_permission(override: Any) -> dict[str, Any]:
    perm = {**DEFAULT_OPENCODE_PERMISSION, "bash": dict(DEFAULT_OPENCODE_PERMISSION["bash"])}
    if not isinstance(override, dict):
        return perm
    for key, value in override.items():
        if key == "bash" and isinstance(value, dict):
            perm["bash"].update(value)
        else:
            perm[key] = value
    return perm


def validate_config(cfg: Config) -> None:
    if cfg.safety.allow_unrestricted:
        return
    claude = cfg.agents["claude"]
    if claude.permission_mode == "bypassPermissions" or "--dangerously-skip-permissions" in claude.extra_args:
        raise ValueError("unrestricted Claude permissions require safety.allow_unrestricted = true")
    bash = cfg.agents["opencode"].permission.get("bash")
    if not isinstance(bash, dict) or "deny" not in bash.values():
        raise ValueError("OpenCode bash permission needs at least one deny rule unless safety.allow_unrestricted = true")
