from datetime import UTC, datetime

from ai_development_orchestrator import failures
from ai_development_orchestrator.models import FailureKind


def test_classify_usage_limit():
    assert failures.classify("usage limit exceeded", False, 1) == FailureKind.usage_limit


def test_classify_auth():
    assert failures.classify("unauthorized api key", False, 401) == FailureKind.auth


def test_classify_network():
    assert failures.classify("network error: connection failed", False, 1) == FailureKind.network


def test_classify_timeout():
    assert failures.classify("x", True, 1) == FailureKind.timeout


def test_parse_epoch():
    now = datetime.now(UTC)
    r = failures.parse_reset_time("reset | 1893456000", now)
    assert r is not None


def test_parse_try_again():
    now = datetime.now(UTC)
    r = failures.parse_reset_time("try again in 5 minutes", now)
    assert r is not None


def test_parse_resets_time():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    r = failures.parse_reset_time("resets 3pm (America/Sao_Paulo)", now)
    assert r is not None


def test_classify_patterns():
    from ai_development_orchestrator import failures
    from ai_development_orchestrator.models import FailureKind

    assert failures.classify("rate limit", False, 1) == FailureKind.usage_limit
    assert failures.classify("limit reached", False, 1) == FailureKind.usage_limit
    assert failures.classify("hit your limit", False, 1) == FailureKind.usage_limit
    assert failures.classify("quota exceeded", False, 1) == FailureKind.usage_limit
    assert failures.classify("too many requests", False, 1) == FailureKind.usage_limit
    assert failures.classify("credit balance low", False, 1) == FailureKind.usage_limit
    assert failures.classify("out of credits", False, 1) == FailureKind.usage_limit
    assert failures.classify("error 429", False, 1) == FailureKind.usage_limit

    assert failures.classify("authentication failed", False, 1) == FailureKind.auth
    assert failures.classify("unauthenticated", False, 1) == FailureKind.auth
    assert failures.classify("invalid api key", False, 1) == FailureKind.auth
    assert failures.classify("invalid x-api-key", False, 1) == FailureKind.auth
    assert failures.classify("/login required", False, 1) == FailureKind.auth
    assert failures.classify("not logged in", False, 1) == FailureKind.auth
    assert failures.classify("please log in", False, 1) == FailureKind.auth
    assert failures.classify("oauth token expired", False, 1) == FailureKind.auth
    assert failures.classify("401", False, 1) == FailureKind.auth
    assert failures.classify("403", False, 1) == FailureKind.auth

    assert failures.classify("ECONNRESET", False, 1) == FailureKind.network
    assert failures.classify("ECONNREFUSED", False, 1) == FailureKind.network
    assert failures.classify("ETIMEDOUT", False, 1) == FailureKind.network
    assert failures.classify("ENOTFOUND", False, 1) == FailureKind.network
    assert failures.classify("EAI_AGAIN", False, 1) == FailureKind.network
    assert failures.classify("network error", False, 1) == FailureKind.network
    assert failures.classify("connection error", False, 1) == FailureKind.network
    assert failures.classify("connection refused", False, 1) == FailureKind.network
    assert failures.classify("connection reset", False, 1) == FailureKind.network
    assert failures.classify("socket hang up", False, 1) == FailureKind.network
    assert failures.classify("overloaded", False, 1) == FailureKind.network
    assert failures.classify("service unavailable", False, 1) == FailureKind.network
    assert failures.classify("bad gateway", False, 1) == FailureKind.network
    assert failures.classify("gateway timeout", False, 1) == FailureKind.network
    assert failures.classify("internal server error", False, 1) == FailureKind.network
    assert failures.classify("500", False, 1) == FailureKind.network
    assert failures.classify("502", False, 1) == FailureKind.network
    assert failures.classify("503", False, 1) == FailureKind.network
    assert failures.classify("504", False, 1) == FailureKind.network
    assert failures.classify("529", False, 1) == FailureKind.network

    assert failures.classify("x", False, 1, api_status=429) == FailureKind.usage_limit
    assert failures.classify("x", False, 1, api_status=401) == FailureKind.auth
    assert failures.classify("x", False, 1, api_status=403) == FailureKind.auth
    assert failures.classify("x", False, 1, api_status=500) == FailureKind.network
    assert failures.classify("x", False, 1, api_status=502) == FailureKind.network
    assert failures.classify("x", False, 1, api_status=503) == FailureKind.network
    assert failures.classify("x", False, 1, api_status=504) == FailureKind.network
    assert failures.classify("x", False, 1, api_status=529) == FailureKind.network


def test_classify_precedence():
    from ai_development_orchestrator import failures
    from ai_development_orchestrator.models import FailureKind

    assert failures.classify("429 unauthorized", False, 1) == FailureKind.usage_limit
