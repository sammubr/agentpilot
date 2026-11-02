from __future__ import annotations

import json

from typer.testing import CliRunner

from ai_development_orchestrator.cli import app
from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import Feature, FeatureStatus, RunStatus


def _add_feature(workspace) -> None:
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="convergence coverage"))
    workspace.save_state(state)


def test_rejection_runs_one_correction_then_completes(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"converge": ["reject", "ok"]}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    result = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run()

    state = workspace.load_state()
    assert result == RunStatus.finished
    assert state.features[0].status == FeatureStatus.completed
    assert state.features[0].correction_round == 1
    steps = [record["step"] for record in calls(path)]
    assert steps.count("converge.0") == 1
    assert steps.count("correct.handoff.1") == 1
    assert steps.count("correct.run.1") == 1
    assert steps.count("converge.1") == 1
    run = next(record for record in calls(path) if record["step"] == "correct.run.1")
    assert "handoff-correction-1.md" in run["argv"][-1]
    assert "round=1" in run["argv"][-1]


def test_exhausted_rejections_block_with_findings_and_validation_exit(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario(
        {
            "claude": {"converge": "reject"},
            "opencode": {},
        }
    )
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.convergence.max_corrections = 2
    config.validation.commands = ["exit 7"]

    result = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()

    state = workspace.load_state()
    feature = state.features[0]
    assert result == RunStatus.halted
    assert feature.status == FeatureStatus.blocked
    assert feature.block_reason is not None
    assert "needs changes" in feature.block_reason
    assert "major: fix needed @ unspecified" in feature.block_reason
    assert "exit 7 (exit 7)" in feature.block_reason
    steps = [record["step"] for record in calls(path)]
    assert [step for step in steps if step.startswith("converge.")] == ["converge.0", "converge.1", "converge.2"]
    assert [step for step in steps if step.startswith("correct.handoff.")] == ["correct.handoff.1", "correct.handoff.2"]
    assert [step for step in steps if step.startswith("correct.run.")] == ["correct.run.1", "correct.run.2"]


def test_failed_validation_is_recorded_then_correction_can_complete(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {}, "opencode": {"implement.run": "break", "correct.run.1": "ok"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.convergence.max_corrections = 2

    result = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()

    state = workspace.load_state()
    feature = state.features[0]
    validation = json.loads((workspace.repo / feature.spec_dir / "orchestrator" / "validation-0.json").read_text())
    assert result == RunStatus.finished
    assert feature.status == FeatureStatus.completed
    assert feature.correction_round == 1
    assert validation[0]["command"] == "test ! -f BROKEN"
    assert validation[0]["passed"] is False
    assert validation[0]["exit_code"] == 1
    steps = [record["step"] for record in calls(path)]
    assert steps.index("correct.run.1") < steps.index("converge.1")


def test_retry_preserves_base_commit_and_original_diff(workspace, scenario, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"converge": ["reject", "ok"]}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.convergence.max_corrections = 0

    first = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()
    state = workspace.load_state()
    original_base = state.features[0].base_commit
    spec_dir = state.features[0].spec_dir
    assert first == RunStatus.halted
    assert state.features[0].status == FeatureStatus.blocked
    assert original_base
    assert spec_dir

    retry = CliRunner().invoke(app, ["--repo", str(workspace.repo), "retry", "F1"])
    assert retry.exit_code == 0, retry.output
    resumed = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()

    state = workspace.load_state()
    diff = (workspace.repo / spec_dir / "orchestrator" / "diff-0.patch").read_text()
    assert resumed == RunStatus.finished
    assert state.features[0].base_commit == original_base
    assert "src/F1.txt" in diff


def test_correction_without_changes_fails_after_execution_retries(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"converge": "reject"}, "opencode": {"correct.run.1": "no_changes"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    result = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run()

    feature = workspace.load_state().features[0]
    assert result == RunStatus.halted
    assert feature.status == FeatureStatus.failed
    assert feature.block_reason == "implementation produced no changes"
    steps = [record["step"] for record in calls(path)]
    assert steps.count("correct.run.1") == 3
    assert "converge.1" not in steps


def test_handoff_validation_commands_run_when_config_has_none(workspace, scenario, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {}, "opencode": {"implement.run": "break", "correct.run.1": "ok"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.validation.commands = []

    result = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()

    feature = workspace.load_state().features[0]
    validation = json.loads((workspace.repo / feature.spec_dir / "orchestrator" / "validation-0.json").read_text())
    assert result == RunStatus.finished
    assert feature.validation_commands == ["test ! -f BROKEN"]
    assert validation[0]["command"] == "test ! -f BROKEN"
    assert validation[0]["passed"] is False
    assert "Handoff validation commands: test ! -f BROKEN" in workspace.report_path.read_text()


def test_handoff_without_validation_commands_is_rejected(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"implement.handoff": "no_validation_file"}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.validation.commands = []

    result = Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()

    feature = workspace.load_state().features[0]
    assert result == RunStatus.halted
    assert feature.status == FeatureStatus.failed
    assert "validation-commands.json" in feature.block_reason
    assert "implement.run" not in [record["step"] for record in calls(path)]
