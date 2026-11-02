from __future__ import annotations

import json

from typer.testing import CliRunner

from ai_development_orchestrator.cli import app
from ai_development_orchestrator.models import Feature, FeatureStatus, Stage

runner = CliRunner()


def invoke(workspace, *args: str, env: dict | None = None):
    return runner.invoke(app, ["--repo", str(workspace.repo), *args], env=env)


def test_cli_lifecycle_and_status(workspace, scenario):
    assert invoke(workspace, "add", "F1", "First", "--description", "Feature one").exit_code == 0
    backlog = workspace.orch / "backlog.yaml"
    backlog.write_text("features:\n  - id: F2\n    title: Second\n    description: Feature two\n    depends_on: [F1]\n")
    assert invoke(workspace, "import", str(backlog)).exit_code == 0
    check = invoke(workspace, "check")
    assert check.exit_code == 0, check.output

    result = invoke(workspace, "run", env={"FAKE_AGENT_SCENARIO": scenario({})})

    assert result.exit_code == 0, result.output
    assert "Run finished" in result.output
    assert [f.status for f in workspace.load_state().features] == [FeatureStatus.completed] * 2
    text = invoke(workspace, "status")
    assert text.exit_code == 0 and ": finished" in text.output and "F2 (depends on F1): completed" in text.output
    data = json.loads(invoke(workspace, "status", "--json").output)
    assert set(data) == {"run", "running", "unavailable", "features"}
    assert data["run"]["status"] == "finished" and data["running"] is False
    assert set(data["features"][0]) == {"id", "title", "status", "spec_dir", "done", "round", "attempts", "last_error", "depends_on"}
    events = {json.loads(line)["event"] for line in workspace.events_path.read_text().splitlines()}
    assert {"run_started", "stage_started", "agent_finished", "stage_completed", "feature_completed", "run_ended"} <= events


def test_init_creates_only_owned_files(target_repo):
    args = ["--repo", str(target_repo), "init"]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args).exit_code == 1
    orch = target_repo / ".orchestrator"
    files = sorted(p.relative_to(orch).as_posix() for p in orch.rglob("*") if p.is_file())
    assert files == [".gitignore", "config.toml", "logs/events.jsonl", "state.json"]


def test_removed_commands_are_usage_errors(workspace):
    assert invoke(workspace, "report").exit_code == 2
    assert invoke(workspace, "retry", "F1", "--from", "plan").exit_code == 2


def test_retry_requeues_blocked_feature(workspace):
    state = workspace.load_state()
    state.features.append(Feature(
        id="F1", title="Feature", description="retry", status=FeatureStatus.blocked, done=[Stage.specify],
        attempts=2, round=3, session_id="s", last_error="open tasks remain",
    ))
    state.features.append(Feature(id="F2", title="Feature", description="pending"))
    workspace.save_state(state)

    assert invoke(workspace, "retry", "F1").exit_code == 0
    feature = workspace.load_state().features[0]
    assert feature.status == FeatureStatus.pending
    assert (feature.attempts, feature.round, feature.session_id, feature.last_error) == (0, 0, None, None)
    assert feature.done == [Stage.specify]
    assert invoke(workspace, "retry", "F2").exit_code == 1
    assert invoke(workspace, "retry", "nope").exit_code == 1


def test_check_reports_missing_opencode_command(workspace):
    (workspace.repo / ".opencode" / "commands" / "speckit.implement.md").unlink()
    result = invoke(workspace, "check")
    assert result.exit_code == 1
    assert "speckit.implement.md" in result.output


def test_pause_and_lock_exit_code(workspace):
    assert invoke(workspace, "pause").exit_code == 0
    assert workspace.read_control() == "pause"
    workspace.acquire_lock()
    try:
        assert invoke(workspace, "add", "F1", "Feature", "--description", "locked").exit_code == 3
    finally:
        workspace.release_lock()
