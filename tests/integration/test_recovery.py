from __future__ import annotations

import json
import os
from datetime import timedelta

import pytest

from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import (
    Feature,
    FeatureStatus,
    PendingRecovery,
    RunStatus,
    utcnow,
)


def _feature(workspace):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="exercise recovery"))
    workspace.save_state(state)


def _run(workspace, scenario_path, sleep):
    os.environ["FAKE_AGENT_SCENARIO"] = scenario_path
    return Engine(workspace, load_config(workspace.config_path), sleep=sleep, handle_signals=False).run()


def test_usage_limit_waits_then_retries_only_failed_step(workspace, scenario, calls):
    _feature(workspace)
    path = scenario({"claude": {"tasks": ["usage_limit", "ok"]}, "opencode": {}})
    waits = []
    assert _run(workspace, path, waits.append) == RunStatus.finished
    records = calls(path)
    assert [record["step"] for record in records].count("tasks") == 2
    assert len(records) == 11
    assert sum(waits) >= load_config(workspace.config_path).retry.usage_limit_initial_delay_s


def test_usage_limit_reset_waits_until_reset(workspace, scenario, calls):
    _feature(workspace)
    path = scenario({"claude": {"tasks": ["usage_limit_reset", "ok"]}, "opencode": {}})
    waits = []
    assert _run(workspace, path, waits.append) == RunStatus.finished
    assert 0.5 <= sum(waits) <= 1.5
    assert [record["step"] for record in calls(path)].count("tasks") == 2


def test_network_failure_retries_opencode_step(workspace, scenario, calls):
    _feature(workspace)
    path = scenario({"claude": {}, "opencode": {"implement.run": ["network", "ok"]}})
    waits = []
    assert _run(workspace, path, waits.append) == RunStatus.finished
    records = calls(path)
    assert [record["step"] for record in records].count("implement.run") == 2
    assert sum(record["step"] == "tasks" for record in records) == 1
    assert sum(waits) > 0


def test_execution_crashes_exhaust_attempts_and_halt(workspace, scenario, calls):
    _feature(workspace)
    path = scenario({"claude": {"tasks": "crash"}, "opencode": {}})
    result = _run(workspace, path, lambda _: None)
    state = workspace.load_state()
    assert result == RunStatus.halted
    assert state.features[0].status == FeatureStatus.failed
    assert state.features[0].attempts["tasks"] == load_config(workspace.config_path).retry.max_attempts_execution
    assert [record["step"] for record in calls(path)].count("tasks") == state.features[0].attempts["tasks"]


def test_invalid_output_retries(workspace, scenario, calls):
    _feature(workspace)
    path = scenario({"claude": {"tasks": ["invalid", "ok"]}, "opencode": {}})
    assert _run(workspace, path, lambda _: None) == RunStatus.finished
    assert [record["step"] for record in calls(path)].count("tasks") == 2


def test_timeout_kills_agent_and_fails_after_configured_attempts(workspace, scenario, calls):
    _feature(workspace)
    path = scenario({"claude": {"tasks": "hang"}, "opencode": {}})
    config = load_config(workspace.config_path)
    config.agents["claude"].timeout_s = 2
    config.retry.max_attempts_execution = 1
    os.environ["FAKE_AGENT_SCENARIO"] = path
    assert Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run() == RunStatus.halted
    record = next(record for record in calls(path) if record["step"] == "tasks")
    assert record["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(record["pid"], 0)
    events = [json.loads(line) for line in workspace.events_path.read_text().splitlines()]
    assert any(event.get("failure") == "timeout" for event in events if event["event"] == "agent_finished")


def test_auth_pauses_then_resumes_same_step(workspace, scenario, calls, tmp_path):
    _feature(workspace)
    path = scenario({"claude": {"tasks": "auth"}, "opencode": {}})
    assert _run(workspace, path, lambda _: None) == RunStatus.paused
    paused = workspace.load_state()
    assert paused.run.status_reason.startswith("auth:")
    from pathlib import Path

    Path(path).write_text('{"claude": {"tasks": "ok"}, "opencode": {}}')
    assert _run(workspace, path, lambda _: None) == RunStatus.finished
    records = calls(path)
    assert [record["step"] for record in records].count("tasks") == 2
    assert [record["step"] for record in records].count("select") == 1


def test_restart_waits_only_remaining_pending_recovery_delay(workspace, scenario, calls):
    state = workspace.load_state()
    feature = Feature(
        id="F1",
        title="Feature",
        description="resume a pending step",
        status=FeatureStatus.in_progress,
        spec_dir="specs/001-f1",
        completed_steps=["select", "specify", "clarify", "plan"],
    )
    state.features.append(feature)
    state.run.active_step = "tasks"
    state.run.current_feature = "F1"
    state.run.pending_recovery = PendingRecovery(
        feature_id="F1", step="tasks", kind="usage_limit", attempt=1,
        next_retry_at=utcnow() + timedelta(seconds=30), message="rate limit",
    )
    workspace.save_state(state)
    path = scenario({"claude": {}, "opencode": {}})
    waits = []
    assert _run(workspace, path, waits.append) == RunStatus.finished
    assert 29.9 <= sum(waits) <= 30.1
    assert calls(path)[0]["step"] == "tasks"
