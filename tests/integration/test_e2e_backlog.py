from __future__ import annotations

import json

from typer.testing import CliRunner

from ai_development_orchestrator.cli import app
from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import FeatureStatus, RunStatus

runner = CliRunner()
BACKLOG = """features:
  - {id: A, title: Alpha, description: First}
  - {id: B, title: Beta, description: Needs A, depends_on: [A]}
  - {id: C, title: Gamma, description: Independent}
"""


def import_backlog(workspace, text=BACKLOG):
    path = workspace.orch / "backlog.yaml"
    path.write_text(text)
    return runner.invoke(app, ["--repo", str(workspace.repo), "import", str(path)])


def engine(workspace, **cfg):
    config = load_config(workspace.config_path)
    config.convergence.max_corrections = cfg.get("max_corrections", 3)
    return Engine(workspace, config, sleep=lambda _: None, handle_signals=False)


def completion_order(workspace):
    events = [line for line in workspace.events_path.read_text().splitlines() if '"feature_completed"' in line]
    return [json.loads(line)["feature"] for line in events]


def test_backlog_runs_in_order_with_own_spec_dirs_and_branches(workspace, scenario, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({}))

    assert engine(workspace).run() == RunStatus.finished

    state = workspace.load_state()
    assert completion_order(workspace) == ["A", "B", "C"]
    assert [f.spec_dir for f in state.features] == ["specs/001-alpha", "specs/002-beta", "specs/003-gamma"]
    assert [f.branch for f in state.features] == ["orchestrator/A", "orchestrator/B", "orchestrator/C"]


def test_blocked_dependency_skips_dependent_and_continues(workspace, scenario, calls, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    path = scenario({"claude": {"converge:alpha": "append"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert engine(workspace, max_corrections=0).run() == RunStatus.finished

    state = workspace.load_state()
    assert [f.status for f in state.features] == [FeatureStatus.blocked, FeatureStatus.pending, FeatureStatus.completed]
    assert state.run.reason == "B waits on A"
    assert "beta" not in {r["feature"] for r in calls(path)}


def test_import_rejects_cycles_and_unknown_dependencies(workspace):
    cycle = "features:\n  - {id: A, title: a, description: a, depends_on: [B]}\n  - {id: B, title: b, description: b, depends_on: [A]}\n"
    result = import_backlog(workspace, cycle)
    assert result.exit_code == 1 and "cycle" in result.output
    unknown = "features:\n  - {id: A, title: a, description: a, depends_on: [Z]}\n"
    result = import_backlog(workspace, unknown)
    assert result.exit_code == 1 and "Z" in result.output
    assert workspace.load_state().features == []


def test_max_features_stops_idle(workspace, scenario, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({}))

    assert engine(workspace).run(max_features=1) == RunStatus.idle

    assert [f.status for f in workspace.load_state().features] == [FeatureStatus.completed, FeatureStatus.pending, FeatureStatus.pending]


def test_specify_retry_never_targets_another_features_spec_dir(workspace, scenario, calls, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    path = scenario({"claude": {"specify:beta": ["no_artifact", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert engine(workspace).run(max_features=2) == RunStatus.idle

    beta_specify = [r for r in calls(path) if r["stage"] == "specify" and r["feature"] == "beta"]
    assert len(beta_specify) == 2
    assert all(r["spec_dir"] is None for r in beta_specify)
    assert (workspace.repo / "specs/001-alpha/spec.md").read_text() == "# Alpha: First\n"
    assert workspace.load_state().features[1].spec_dir == "specs/002-beta"
