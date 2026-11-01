from __future__ import annotations

import os
import subprocess

from ai_development_orchestrator.backlog import add_features, load_backlog_file
from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import FeatureStatus, RunStatus


def _import_yaml(tmp_path, workspace):
    backlog_path = tmp_path / "backlog.yaml"
    backlog_path.write_text(
        "features:\n"
        "  - id: F1\n    title: First\n    description: first\n"
        "  - id: F2\n    title: Second\n    description: second\n    depends_on: [F1]\n"
        "  - id: F3\n    title: Third\n    description: third\n"
    )
    state = workspace.load_state()
    add_features(state, load_backlog_file(backlog_path))
    workspace.save_state(state)


def test_backlog_completes_in_dependency_order_and_forks_checkpoint(target_repo, workspace, scenario, calls, tmp_path):
    _import_yaml(tmp_path, workspace)
    scenario_path = scenario({"claude": {}, "opencode": {}})
    os.environ["FAKE_AGENT_SCENARIO"] = scenario_path
    result = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run()
    state = workspace.load_state()
    assert result == RunStatus.finished
    assert [feature.status for feature in state.features] == [FeatureStatus.completed] * 3
    call_log = calls(scenario_path)
    feature_order = []
    for record in call_log:
        if record["feature"] not in feature_order:
            feature_order.append(record["feature"])
    assert feature_order == ["F1", "F2", "F3"]
    expected_steps = {"select", "specify", "clarify", "plan", "tasks", "analyze", "implement.handoff", "implement.run", "post_analyze", "converge.0"}
    for feature_id in ("F1", "F2", "F3"):
        feature_calls = [record for record in call_log if record["feature"] == feature_id]
        assert {record["step"] for record in feature_calls} == expected_steps
        assert len(feature_calls) == len(expected_steps)
    f1 = next(feature for feature in state.features if feature.id == "F1")
    f2 = next(feature for feature in state.features if feature.id == "F2")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", f1.branch, f2.base_commit],
        cwd=workspace.repo,
        check=False,
    )
    assert ancestor.returncode == 0


def test_blocked_feature_halts_backlog_and_keeps_later_features_pending(target_repo, workspace, scenario, tmp_path):
    _import_yaml(tmp_path, workspace)
    scenario_path = scenario({"claude": {"converge.0": "reject"}, "opencode": {}})
    os.environ["FAKE_AGENT_SCENARIO"] = scenario_path
    config = load_config(workspace.config_path)
    config.convergence.max_corrections = 0
    result = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()
    state = workspace.load_state()
    assert result == RunStatus.halted
    assert state.features[0].status == FeatureStatus.blocked
    assert [feature.status for feature in state.features[1:]] == [FeatureStatus.pending, FeatureStatus.pending]
    assert "F1" in (state.run.status_reason or "")


def test_select_adds_dependency_and_processes_declared_feature_first(target_repo, workspace, scenario, calls, tmp_path):
    _import_yaml(tmp_path, workspace)
    scenario_path = scenario({"claude": {"select:F2": "dep:F3"}, "opencode": {}})
    os.environ["FAKE_AGENT_SCENARIO"] = scenario_path
    result = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run()
    state = workspace.load_state()
    f2 = next(feature for feature in state.features if feature.id == "F2")
    dependency = next(item for item in f2.dependencies if item.id == "F3")
    assert result == RunStatus.finished
    assert (dependency.rationale, dependency.added_by) == ("fake", "claude")
    order = []
    for record in calls(scenario_path):
        if record["step"] == "implement.run" and record["feature"] not in order:
            order.append(record["feature"])
    assert order == ["F1", "F3", "F2"]
    events = [line for line in workspace.events_path.read_text().splitlines() if '"dependency_added"' in line]
    assert any('"rationale": "fake"' in line and '"added_by": "claude"' in line for line in events)
