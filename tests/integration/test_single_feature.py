from __future__ import annotations

import subprocess

from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import Feature, FeatureStatus, RunStatus

ORDER = ["specify", "clarify", "plan", "tasks", "analyze", "implement", "converge"]


def add(workspace, *features):
    state = workspace.load_state()
    state.features.extend(features)
    workspace.save_state(state)


def run_engine(workspace, **kwargs):
    return Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run(**kwargs)


def git_log(repo, branch):
    out = subprocess.run(["git", "log", "--format=%s", branch], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.split("\n")


def test_feature_runs_seven_spec_kit_calls(workspace, scenario, calls, monkeypatch):
    add(workspace, Feature(id="F1", title="Login", description="Users log in"))
    path = scenario({"claude": {}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert run_engine(workspace) == RunStatus.finished

    records = calls(path)
    assert [r["stage"] for r in records] == ORDER
    assert [r["agent"] for r in records] == ["claude"] * 5 + ["opencode", "claude"]
    assert all(r["spec_dir"] == "specs/001-login" for r in records[1:])
    assert records[0]["spec_dir"] is None
    assert all(len(r["message"]) < 500 for r in records[1:])
    assert records[0]["message"].startswith("/speckit-specify Login: Users log in")
    state = workspace.load_state()
    feature = state.features[0]
    assert feature.status == FeatureStatus.completed
    assert feature.spec_dir == "specs/001-login"
    assert feature.branch == "orchestrator/F1"
    assert state.run.feature is None


def test_commits_only_for_stages_with_changes(workspace, scenario, monkeypatch):
    add(workspace, Feature(id="F1", title="Login", description="Users log in"))
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({"claude": {}, "opencode": {}}))

    run_engine(workspace)

    subjects = [s for s in git_log(workspace.repo, "orchestrator/F1") if s.startswith("orchestrator(")]
    assert subjects == [f"orchestrator(F1): {stage}" for stage in ("implement", "tasks", "plan", "specify")]


def test_orchestrator_writes_nothing_in_specs_or_specify(workspace, scenario, monkeypatch):
    add(workspace, Feature(id="F1", title="Login", description="Users log in"))
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({"claude": {}, "opencode": {}}))

    run_engine(workspace)

    spec_files = sorted(p.relative_to(workspace.repo).as_posix() for p in (workspace.repo / "specs").rglob("*") if p.is_file())
    assert spec_files == ["specs/001-login/plan.md", "specs/001-login/spec.md", "specs/001-login/tasks.md"]
    owned = sorted(p.relative_to(workspace.orch).as_posix() for p in workspace.orch.rglob("*") if p.is_file())
    assert owned == [".gitignore", "config.toml", "lock", "logs/events.jsonl", "state.json"]
