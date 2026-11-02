from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import AgentName, Feature, FeatureStatus, RunStatus, utcnow

STAGES = ["specify", "clarify", "plan", "tasks", "analyze", "implement", "converge"]
ENGINE = (
    "from pathlib import Path; from ai_development_orchestrator.config import load_config; "
    "from ai_development_orchestrator.engine import Engine; from ai_development_orchestrator.workspace import Workspace; "
    "w=Workspace(Path.cwd()); Engine(w, load_config(w.config_path)).run()"
)


def add_feature(workspace, **kw):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Login", description="Users log in", **kw))
    workspace.save_state(state)


def engine(workspace, sleep=lambda _: None, **cfg):
    config = load_config(workspace.config_path)
    for key, value in cfg.items():
        section, name = key.split("__")
        setattr(getattr(config, section), name, value)
    return Engine(workspace, config, sleep=sleep, handle_signals=False)


def run_killed(workspace, scenario_path):
    env = {**os.environ, "FAKE_AGENT_SCENARIO": scenario_path}
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", ENGINE], cwd=workspace.repo, env=env, check=False)
    assert result.returncode == -9


@pytest.mark.parametrize("stage", STAGES)
def test_kill_during_stage_repeats_only_that_stage(workspace, scenario, calls, monkeypatch, stage):
    add_feature(workspace)
    agent = "opencode" if stage == "implement" else "claude"
    path = scenario({agent: {stage: ["kill_parent", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    run_killed(workspace, path)
    assert engine(workspace).run() == RunStatus.finished

    stages = [r["stage"] for r in calls(path)]
    assert stages == STAGES[: STAGES.index(stage) + 1] + STAGES[STAGES.index(stage):]
    assert workspace.load_state().features[0].status == FeatureStatus.completed


def test_kill_during_implement_keeps_partial_work(workspace, scenario, calls, monkeypatch):
    add_feature(workspace)
    path = scenario({"opencode": {"implement": ["partial_kill", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    run_killed(workspace, path)
    tasks = (workspace.repo / "specs/001-login/tasks.md").read_text()
    assert "- [X] T001" in tasks and "- [ ] T002" in tasks
    assert engine(workspace).run() == RunStatus.finished

    assert [r["stage"] for r in calls(path)][5:] == ["implement", "implement", "converge"]
    assert workspace.load_state().features[0].status == FeatureStatus.completed


def test_missing_plan_reruns_plan_tasks_and_analyze(workspace, scenario, calls, monkeypatch):
    add_feature(workspace)
    path = scenario({"claude": {"converge": ["append", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    assert engine(workspace, convergence__max_corrections=0).run() == RunStatus.finished
    assert workspace.load_state().features[0].status == FeatureStatus.blocked

    (workspace.repo / "specs/001-login/plan.md").unlink()
    subprocess.run(["git", "commit", "-qam", "operator removes plan"], cwd=workspace.repo, check=True)
    state = workspace.load_state()
    state.features[0].status = FeatureStatus.pending
    workspace.save_state(state)
    assert engine(workspace).run() == RunStatus.finished

    assert [r["stage"] for r in calls(path)][7:] == ["plan", "tasks", "analyze", "implement", "converge"]
    assert workspace.load_state().features[0].status == FeatureStatus.completed


def test_v1_state_resumes_at_implement(workspace, scenario, calls, monkeypatch):
    spec = workspace.repo / "specs/001-login"
    spec.mkdir(parents=True)
    for name, text in (("spec.md", "x"), ("plan.md", "x"), ("tasks.md", "- [ ] T001 a\n")):
        (spec / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=workspace.repo, check=True)
    subprocess.run(["git", "commit", "-qm", "specs"], cwd=workspace.repo, check=True)
    workspace.state_path.write_text(json.dumps({"version": 1, "features": [{
        "id": "F1", "title": "Login", "description": "d", "status": "in_progress", "spec_dir": "specs/001-login",
        "completed_steps": ["select", "specify", "clarify", "plan", "tasks", "analyze"],
    }]}))
    path = scenario({})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert engine(workspace).run() == RunStatus.finished

    assert [r["stage"] for r in calls(path)] == ["implement", "converge"]


def test_usage_limit_with_reset_waits_then_repeats_stage(workspace, scenario, calls, monkeypatch):
    add_feature(workspace)
    path = scenario({"opencode": {"implement": ["usage_limit_reset", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    snapshots = []

    def sleep(seconds):
        snapshots.append((seconds, workspace.load_state(), len(calls(path))))

    assert engine(workspace, sleep=sleep).run() == RunStatus.finished

    assert sum(s for s, _, _ in snapshots) >= 1
    assert all(state.unavailable.get(AgentName.opencode) for _, state, _ in snapshots)
    assert all(state.run.status == RunStatus.waiting for _, state, _ in snapshots)
    assert len({count for _, _, count in snapshots}) == 1
    assert [r["stage"] for r in calls(path)][5:] == ["implement", "implement", "converge"]
    state = workspace.load_state()
    assert state.features[0].status == FeatureStatus.completed
    assert state.unavailable == {}


def test_usage_limit_without_reset_keeps_waiting_with_growing_delay(workspace, scenario, calls, monkeypatch):
    add_feature(workspace)
    path = scenario({"claude": {"plan": ["usage_limit"] * 5 + ["ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    sleeps = []

    run = engine(workspace, sleep=sleeps.append, retry__usage_limit_initial_delay_s=1, retry__usage_limit_max_wait_s=6)
    assert run.run() == RunStatus.finished

    assert sum(sleeps) == pytest.approx(1 + 2 + 4 + 6 + 6, abs=0.5)
    feature = workspace.load_state().features[0]
    assert feature.status == FeatureStatus.completed
    assert feature.attempts == 0


def test_stored_unavailability_respected_after_restart(workspace, scenario, calls, monkeypatch):
    add_feature(workspace)
    state = workspace.load_state()
    state.unavailable[AgentName.claude] = utcnow() + timedelta(seconds=5)
    workspace.save_state(state)
    path = scenario({})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    sleeps = []

    def sleep(seconds):
        assert calls(path) == []
        sleeps.append(seconds)

    assert engine(workspace, sleep=sleep).run() == RunStatus.finished
    assert sum(sleeps) == pytest.approx(5, abs=0.5)


def test_network_failures_are_bounded(workspace, scenario, calls, monkeypatch):
    add_feature(workspace)
    path = scenario({"claude": {"plan": "network"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert engine(workspace).run() == RunStatus.finished

    assert [r["stage"] for r in calls(path)].count("plan") == 10
    feature = workspace.load_state().features[0]
    assert feature.status == FeatureStatus.blocked
    assert "network" in feature.last_error


def test_auth_failure_pauses_until_resume(workspace, scenario, calls, monkeypatch, drive):
    add_feature(workspace)
    path = scenario({"claude": {"plan": ["auth", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)

    assert drive(engine(workspace), controls=("resume",)) == RunStatus.finished

    events = [json.loads(line) for line in workspace.events_path.read_text().splitlines()]
    paused = [e for e in events if e["event"] == "paused"]
    assert len(paused) == 1 and paused[0]["reason"].startswith("auth: unauthorized")
    assert [r["stage"] for r in calls(path)].count("plan") == 2
    assert workspace.load_state().features[0].status == FeatureStatus.completed
