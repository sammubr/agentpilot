from datetime import UTC, datetime, timedelta

from ai_development_orchestrator import retry
from ai_development_orchestrator.config import RetryConfig
from ai_development_orchestrator.models import FailureKind


def test_backoff_growth():
    cfg = RetryConfig(initial_delay_s=30, multiplier=2.0, max_delay_s=1800)
    now = datetime.now(UTC)
    d1 = retry.decide(FailureKind.network, 1, None, now, cfg)
    d2 = retry.decide(FailureKind.network, 2, None, now, cfg)
    assert d1.action == "wait"
    assert d2.delay_s > d1.delay_s


def test_network_limit():
    cfg = RetryConfig(max_attempts_network=1)
    now = datetime.now(UTC)
    d = retry.decide(FailureKind.network, 1, None, now, cfg)
    assert d.action == "fail"


def test_execution_limit():
    cfg = RetryConfig(max_attempts_execution=1)
    now = datetime.now(UTC)
    d = retry.decide(FailureKind.execution_error, 1, None, now, cfg)
    assert d.action == "fail"


def test_usage_limit_reset():
    cfg = RetryConfig(usage_limit_margin_s=60, usage_limit_max_wait_s=21600)
    now = datetime.now(UTC)
    reset = now + timedelta(seconds=100)
    d = retry.decide(FailureKind.usage_limit, 1, reset, now, cfg)
    assert d.action == "wait"
    assert d.delay_s >= 60


def test_auth_pause():
    cfg = RetryConfig()
    now = datetime.now(UTC)
    d = retry.decide(FailureKind.auth, 1, None, now, cfg)
    assert d.action == "pause"
