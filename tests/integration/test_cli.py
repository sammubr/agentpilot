from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from ai_development_orchestrator import gitops
from ai_development_orchestrator.cli import app
from ai_development_orchestrator.models import Feature, FeatureStatus

runner = CliRunner()


def invoke(workspace, *args: str, env: dict | None = None):
    return runner.invoke(app, ["--repo", str(workspace.repo), *args], env=env)


def test_cli_lifecycle_and_status(workspace, scenario):
    result = invoke(workspace, "add", "F1", "First", "--description", "Feature one")
    assert result.exit_code == 0, result.output
    backlog = workspace.orch / "backlog.yaml"
    backlog.write_text("features:\n  - id: F2\n    title: Second\n    description: Feature two\n    depends_on: [F1]\n")
    result = invoke(workspace, "import", str(backlog))
    assert result.exit_code == 0, result.output
    result = invoke(workspace, "check")
    assert result.exit_code == 0, result.output
    scenario_path = scenario({})
    result = invoke(workspace, "run", env={"FAKE_AGENT_SCENARIO": scenario_path})
    assert result.exit_code == 0, result.output
    state = workspace.load_state()
    assert [feature.status for feature in state.features] == [FeatureStatus.completed] * 2
    text = invoke(workspace, "status")
    assert text.exit_code == 0 and "Run: finished" in text.output
    structured = invoke(workspace, "status", "--json")
    data = json.loads(structured.output)
    assert data["current_feature"] is None
    assert data["current_step"] is None
    assert set(data) == {
        "run_status", "status_reason", "running", "backlog_position", "backlog_total",
        "current_feature", "current_feature_title", "current_phase", "current_step",
        "current_agent", "run_elapsed_s", "feature_elapsed_s", "step_attempts", "last_error",
        "next_retry_at", "completed", "blocked", "failed", "pending",
    }
    report = invoke(workspace, "report")
    assert report.exit_code == 0
    assert "F1: First" in workspace.report_path.read_text()
    assert "F2: Second" in workspace.report_path.read_text()
    events = [json.loads(line) for line in workspace.events_path.read_text().splitlines()]
    assert {"agent_started", "feature_started", "run_ended"}.issubset({item["event"] for item in events})


def test_cli_init_refuses_existing_config(target_repo):
    args = ["--repo", str(target_repo), "init"]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == 0
    assert second.exit_code == 1


def test_retry_default_and_from(workspace):
    state = workspace.load_state()
    state.features.append(Feature(
        id="F1", title="Feature", description="retry", status=FeatureStatus.blocked,
        completed_steps=["specify", "clarify", "plan", "tasks", "converge.0", "correct.handoff.1"],
        attempts={"plan": 2}, correction_round=1, block_reason="review",
    ))
    workspace.save_state(state)
    result = invoke(workspace, "retry", "F1")
    assert result.exit_code == 0, result.output
    feature = workspace.load_state().features[0]
    assert feature.completed_steps == ["specify", "clarify", "plan", "tasks"]
    assert feature.correction_round == 0 and feature.attempts == {}
    feature.status = FeatureStatus.failed
    feature.completed_steps = ["specify", "clarify", "plan", "tasks"]
    workspace.save_state(workspace.load_state().model_copy(update={"features": [feature]}))
    result = invoke(workspace, "retry", "F1", "--from", "plan")
    assert result.exit_code == 0, result.output
    assert workspace.load_state().features[0].completed_steps == ["specify", "clarify"]


@pytest.mark.parametrize("retry_from", [False, True])
def test_retry_failed_active_step_preserves_partial_output(workspace, scenario, retry_from):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="retry partial output"))
    workspace.save_state(state)
    failed_scenario = scenario({"claude": {"implement.handoff": "bad_handoff"}})

    result = invoke(workspace, "run", env={"FAKE_AGENT_SCENARIO": failed_scenario})
    assert result.exit_code == 1, result.output
    state = workspace.load_state()
    feature = state.features[0]
    assert feature.status == FeatureStatus.failed
    assert state.run.active_step == "implement.handoff"
    assert state.run.current_step == "implement.handoff"
    assert feature.spec_dir
    handoff = workspace.repo / feature.spec_dir / "orchestrator" / "handoff-implement.md"
    assert handoff.exists()
    assert "## Restrictions" not in handoff.read_text()
    assert handoff.relative_to(workspace.repo).as_posix() in gitops.dirty_files(workspace.repo)

    retry_args = ["retry", "F1"]
    if retry_from:
        retry_args.extend(["--from", "implement.handoff"])
    result = invoke(workspace, *retry_args)
    assert result.exit_code == 0, result.output

    successful_scenario = scenario({})
    result = invoke(workspace, "resume", env={"FAKE_AGENT_SCENARIO": successful_scenario})
    assert result.exit_code == 0, result.output
    assert workspace.load_state().features[0].status == FeatureStatus.completed


def test_pause_and_lock_exit_code(workspace):
    result = invoke(workspace, "pause")
    assert result.exit_code == 0
    assert workspace.read_control() == "pause"
    workspace.acquire_lock()
    try:
        result = invoke(workspace, "add", "F1", "Feature", "--description", "locked")
        assert result.exit_code == 3
    finally:
        workspace.release_lock()
