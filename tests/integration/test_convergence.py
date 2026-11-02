from __future__ import annotations

from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import Feature, FeatureStatus, RunStatus


def setup(workspace, scenario, monkeypatch, rules):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Login", description="Users log in"))
    workspace.save_state(state)
    path = scenario(rules)
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    return path


def run_engine(workspace, max_corrections=3, max_attempts=3):
    config = load_config(workspace.config_path)
    config.convergence.max_corrections = max_corrections
    config.retry.max_attempts_execution = max_attempts
    return Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()


def tail(records):
    return [r["stage"] for r in records][5:]


def test_one_correction_round(workspace, scenario, calls, monkeypatch):
    path = setup(workspace, scenario, monkeypatch, {"claude": {"converge": ["append", "ok"]}, "opencode": {}})

    assert run_engine(workspace) == RunStatus.finished

    assert tail(calls(path)) == ["implement", "converge", "implement", "converge"]
    assert len(calls(path)) == 9
    feature = workspace.load_state().features[0]
    assert feature.status == FeatureStatus.completed
    assert feature.round == 1


def test_blocked_after_max_correction_rounds(workspace, scenario, calls, monkeypatch):
    path = setup(workspace, scenario, monkeypatch, {"claude": {"converge": "append"}, "opencode": {}})

    assert run_engine(workspace) == RunStatus.finished

    assert tail(calls(path)) == ["implement", "converge"] * 4
    feature = workspace.load_state().features[0]
    assert feature.status == FeatureStatus.blocked
    assert "open tasks remain after 3 correction rounds" in feature.last_error


def test_open_tasks_after_implement_still_go_through_converge(workspace, scenario, calls, monkeypatch):
    path = setup(workspace, scenario, monkeypatch, {"claude": {}, "opencode": {"implement": ["partial", "ok"]}})

    run_engine(workspace)

    assert tail(calls(path)) == ["implement", "converge", "implement", "converge"]
    assert workspace.load_state().features[0].status == FeatureStatus.completed


def test_missing_artifact_retried_then_blocked(workspace, scenario, calls, monkeypatch):
    path = setup(workspace, scenario, monkeypatch, {"claude": {"plan": "no_artifact"}, "opencode": {}})

    run_engine(workspace, max_attempts=2)

    plan_calls = [r for r in calls(path) if r["stage"] == "plan"]
    assert len(plan_calls) == 2
    assert "Previous attempt rejected: missing specs/001-login/plan.md" in plan_calls[1]["message"]
    feature = workspace.load_state().features[0]
    assert feature.status == FeatureStatus.blocked
    assert "plan.md" in feature.last_error
