from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import RetryConfig
from .models import FailureKind

USAGE_PATTERNS = (r"usage limit", r"rate limit", r"limit reached", r"hit your limit", r"\bquota\b", r"too many requests", r"credit balance", r"out of credits", r"\b429\b")
AUTH_PATTERNS = (r"authentication", r"unauthorized", r"unauthenticated", r"invalid api key", r"invalid x-api-key", r"/login", r"not logged in", r"please log in", r"oauth token", r"\b401\b", r"\b403\b")
NETWORK_PATTERNS = (r"econnreset", r"econnrefused", r"etimedout", r"enotfound", r"eai_again", r"network error", r"connection error", r"connection refused", r"connection reset", r"socket hang up", r"overloaded", r"service unavailable", r"bad gateway", r"gateway timeout", r"internal server error", r"\b500\b", r"\b502\b", r"\b503\b", r"\b504\b", r"\b529\b")


def classify(text: str, timed_out: bool, exit_code: int | None, api_status: int | None = None) -> FailureKind | None:
    if timed_out:
        return FailureKind.timeout
    status = {429: FailureKind.usage_limit, 401: FailureKind.auth, 403: FailureKind.auth, 500: FailureKind.network, 502: FailureKind.network, 503: FailureKind.network, 504: FailureKind.network, 529: FailureKind.network}
    if api_status in status:
        return status[api_status]
    lowered = text.lower()
    for patterns, kind in ((USAGE_PATTERNS, FailureKind.usage_limit), (AUTH_PATTERNS, FailureKind.auth), (NETWORK_PATTERNS, FailureKind.network)):
        if _matches(lowered, patterns):
            return kind
    return None if exit_code == 0 else FailureKind.execution_error


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def parse_reset_time(text: str, now: datetime) -> datetime | None:
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now
    for parser in (_epoch_time, _relative_time, _iso_time, _clock_time):
        result = parser(text, now)
        if result is not None:
            return result
    return None


def _epoch_time(text: str, now: datetime) -> datetime | None:
    match = re.search(r"\|\s*(\d{10,13})\b", text)
    if not match:
        return None
    epoch = int(match.group(1))
    return datetime.fromtimestamp(epoch / 1000 if epoch > 10_000_000_000 else epoch, tz=UTC)


def _relative_time(text: str, now: datetime) -> datetime | None:
    match = re.search(r"try again in (\d+)\s*(second|minute|hour)s?", text, re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    delta = {"second": timedelta(seconds=amount), "minute": timedelta(minutes=amount), "hour": timedelta(hours=amount)}[unit]
    return now + delta


def _iso_time(text: str, now: datetime) -> datetime | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", text)
    if not match:
        return None
    try:
        parsed = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _clock_time(text: str, now: datetime) -> datetime | None:
    match = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?", text, re.IGNORECASE)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    hour = _hour_24(hour, match.group(3))
    zone = _time_zone(match.group(4))
    candidate = now.astimezone(zone).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now.astimezone(zone):
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _hour_24(hour: int, meridiem: str | None) -> int:
    if meridiem and meridiem.lower() == "pm" and hour != 12:
        return hour + 12
    if meridiem and meridiem.lower() == "am" and hour == 12:
        return 0
    return hour % 24


def _time_zone(name: str | None):
    try:
        return ZoneInfo(name) if name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


@dataclass
class Decision:
    action: str
    delay_s: float


def decide(kind: FailureKind, attempt: int, reset_at: datetime | None, now: datetime, cfg: RetryConfig) -> Decision:
    if kind == FailureKind.auth:
        return Decision("pause", 0)
    if kind == FailureKind.usage_limit:
        if reset_at is not None:
            delay = max((reset_at - now).total_seconds() + cfg.usage_limit_margin_s, 1)
        else:
            delay = cfg.usage_limit_initial_delay_s * cfg.multiplier ** max(0, attempt - 1)
        return Decision("wait", float(min(delay, cfg.usage_limit_max_wait_s)))
    limit = cfg.max_attempts_network if kind == FailureKind.network else cfg.max_attempts_execution
    if attempt >= limit:
        return Decision("fail", 0)
    return Decision("wait", float(min(cfg.initial_delay_s * cfg.multiplier ** max(0, attempt - 1), cfg.max_delay_s)))
