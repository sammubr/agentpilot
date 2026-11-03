from __future__ import annotations

import json
import subprocess

from typer.testing import CliRunner

from ai_development_orchestrator import gitops
from ai_development_orchestrator.cli import app
from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import FeatureStatus, RunStatus

runner = CliRunner()
BACKLOG = """features:
  - {id: A, title: Alpha, description: First, depends_on: [C]}
  - {id: B, title: Beta, description: Second, depends_on: [UNKNOWN]}
  - {id: C, title: Gamma, description: Third, depends_on: [A]}
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


def is_ancestor(repo, parent, branch):
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", parent, branch], cwd=repo, check=False
    ).returncode == 0


def test_backlog_runs_in_order_with_own_spec_dirs_and_branches(workspace, scenario, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({}))

    assert engine(workspace).run() == RunStatus.finished

    state = workspace.load_state()
    assert completion_order(workspace) == ["A", "B", "C"]
    assert [f.spec_dir for f in state.features] == ["specs/001-alpha", "specs/002-beta", "specs/003-gamma"]
    assert [f.branch for f in state.features] == ["orchestrator/A", "orchestrator/B", "orchestrator/C"]


def test_feature_branches_stack_and_reuse_existing_next_branch(workspace, scenario, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    initial_branch = gitops.current_branch(workspace.repo)
    subprocess.run(["git", "checkout", "-qb", "default-base"], cwd=workspace.repo, check=True)
    (workspace.repo / "base.txt").write_text("configured base")
    subprocess.run(["git", "add", "base.txt"], cwd=workspace.repo, check=True)
    subprocess.run(["git", "commit", "-m", "configured base"], cwd=workspace.repo, check=True)
    default_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace.repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/default-base", default_head],
        cwd=workspace.repo,
        check=True,
    )
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/default-base"],
        cwd=workspace.repo,
        check=True,
    )
    subprocess.run(["git", "checkout", initial_branch], cwd=workspace.repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "work"], cwd=workspace.repo, check=True)
    (workspace.repo / "work.txt").write_text("work branch")
    subprocess.run(["git", "add", "work.txt"], cwd=workspace.repo, check=True)
    subprocess.run(["git", "commit", "-m", "work branch"], cwd=workspace.repo, check=True)
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({}))

    assert engine(workspace).run(max_features=1) == RunStatus.idle
    state = workspace.load_state()
    branch_a = state.features[0].branch
    assert is_ancestor(workspace.repo, "origin/default-base", branch_a)
    assert not is_ancestor(workspace.repo, "work", branch_a)
    gitops.ensure_branch(workspace.repo, "orchestrator/B", start_point=branch_a)
    gitops.ensure_branch(workspace.repo, branch_a)

    assert engine(workspace).run(max_features=1) == RunStatus.idle
    state = workspace.load_state()
    assert state.features[1].branch == "orchestrator/B"
    assert is_ancestor(workspace.repo, branch_a, state.features[1].branch)

    assert engine(workspace).run() == RunStatus.finished
    state = workspace.load_state()
    assert [feature.branch for feature in state.features] == [
        "orchestrator/A", "orchestrator/B", "orchestrator/C"
    ]
    assert is_ancestor(workspace.repo, state.features[1].branch, state.features[2].branch)


def test_blocked_feature_holds_later_features(workspace, scenario, calls, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    path = scenario({"claude": {"converge:alpha": "append"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert engine(workspace, max_corrections=0).run() == RunStatus.finished

    state = workspace.load_state()
    assert [f.status for f in state.features] == [FeatureStatus.blocked, FeatureStatus.pending, FeatureStatus.pending]
    assert "A" in state.run.reason
    assert not {"beta", "gamma"} & {r["feature"] for r in calls(path)}


def test_retry_blocked_head_then_continue_in_order(workspace, scenario, calls, monkeypatch):
    assert import_backlog(workspace).exit_code == 0
    path = scenario({"claude": {"converge:alpha": ["append", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert engine(workspace, max_corrections=0).run() == RunStatus.finished
    blocked = workspace.load_state()
    assert [feature.status for feature in blocked.features] == [
        FeatureStatus.blocked, FeatureStatus.pending, FeatureStatus.pending
    ]
    assert blocked.features[0].branch
    assert blocked.features[1].branch is None and blocked.features[2].branch is None
    assert not {"beta", "gamma"} & {record["feature"] for record in calls(path)}

    retry = runner.invoke(app, ["--repo", str(workspace.repo), "retry", "A"])
    assert retry.exit_code == 0, retry.output
    assert engine(workspace, max_corrections=1).run() == RunStatus.finished

    state = workspace.load_state()
    assert [feature.status for feature in state.features] == [FeatureStatus.completed] * 3
    assert completion_order(workspace) == ["A", "B", "C"]


def test_import_ignores_cycles_and_unknown_dependencies(workspace):
    legacy = "features:\n  - {id: A, title: a, description: a, depends_on: [B]}\n  - {id: B, title: b, description: b, depends_on: [A]}\n  - {id: C, title: c, description: c, depends_on: [unknown]}\n"
    result = import_backlog(workspace, legacy)

    assert result.exit_code == 0, result.output
    assert [feature.id for feature in workspace.load_state().features] == ["A", "B", "C"]


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
