from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import RetryConfig
from .models import FailureKind


@dataclass
class Decision:
    action: str  # wait|fail|pause
    delay_s: float


def decide(kind: FailureKind, attempt: int, reset_at: datetime | None, now: datetime, retry_cfg: RetryConfig) -> Decision:
    if kind == FailureKind.auth:
        return Decision(action="pause", delay_s=0)
    if kind == FailureKind.usage_limit:
        if reset_at is not None:
            delay = (reset_at - now).total_seconds() + retry_cfg.usage_limit_margin_s
            delay = max(delay, 1)
            delay = min(delay, retry_cfg.usage_limit_max_wait_s)
            return Decision(action="wait", delay_s=float(delay))
        # backoff without reset
        delay = retry_cfg.usage_limit_initial_delay_s * (retry_cfg.multiplier ** max(0, attempt - 1))
        delay = min(delay, retry_cfg.max_delay_s)
        return Decision(action="wait", delay_s=float(delay))
    if kind == FailureKind.network:
        if attempt >= retry_cfg.max_attempts_network:
            return Decision(action="fail", delay_s=0)
        delay = retry_cfg.initial_delay_s * (retry_cfg.multiplier ** max(0, attempt - 1))
        delay = min(delay, retry_cfg.max_delay_s)
        return Decision(action="wait", delay_s=float(delay))
    # timeout, execution_error, invalid_output
    if attempt >= retry_cfg.max_attempts_execution:
        return Decision(action="fail", delay_s=0)
    delay = retry_cfg.initial_delay_s * (retry_cfg.multiplier ** max(0, attempt - 1))
    delay = min(delay, retry_cfg.max_delay_s)
    return Decision(action="wait", delay_s=float(delay))
