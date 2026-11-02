from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from ai_development_orchestrator.models import Feature, FeatureStatus, RunStatus, Stage


def env_for(scenario_path: str) -> dict[str, str]:
    env = {**os.environ, "FAKE_AGENT_SCENARIO": scenario_path}
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def cli(workspace, env, *args) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "ai_development_orchestrator", "--repo", str(workspace.repo), *args]
    return subprocess.run(argv, cwd=workspace.repo, env=env, capture_output=True, text=True, check=False)


def start(workspace, env, command="run") -> subprocess.Popen:
    argv = [sys.executable, "-m", "ai_development_orchestrator", "--repo", str(workspace.repo), command]
    return subprocess.Popen(argv, cwd=workspace.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for(predicate, process=None, timeout=20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if process is not None and process.poll() is not None:
            out, err = process.communicate()
            raise AssertionError(f"process exited early ({process.returncode}): {out}\n{err}")
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def add_feature(workspace):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Login", description="Users log in"))
    workspace.save_state(state)


def stages(calls, path):
    return [r["stage"] for r in calls(path)]


def test_pause_keeps_process_and_resume_continues_same_run(workspace, scenario, calls):
    add_feature(workspace)
    path = scenario({"claude": {"plan": "request_pause"}})
    env = env_for(path)
    process = start(workspace, env)
    wait_for(lambda: workspace.load_state().run.status == RunStatus.paused, process)

    state = workspace.load_state()
    assert Stage.plan in state.features[0].done
    assert workspace.is_locked() and process.poll() is None
    time.sleep(0.5)
    assert stages(calls, path) == ["specify", "clarify", "plan"]
    run_id = state.run.id

    assert cli(workspace, env, "resume").returncode == 0
    assert process.wait(30) == 0
    state = workspace.load_state()
    assert state.run.id == run_id
    assert state.run.status == RunStatus.finished
    assert state.features[0].status == FeatureStatus.completed


def test_resume_without_live_process_restarts_same_run(workspace, scenario, calls):
    add_feature(workspace)
    state = workspace.load_state()
    state.run.id, state.run.status = "abc123", RunStatus.paused
    workspace.save_state(state)
    path = scenario({})

    result = cli(workspace, env_for(path), "resume")

    assert result.returncode == 0, result.stderr
    state = workspace.load_state()
    assert state.run.id == "abc123"
    assert state.features[0].status == FeatureStatus.completed


def test_stop_terminates_agent_and_continue_starts_new_session(workspace, scenario, calls):
    add_feature(workspace)
    path = scenario({"claude": {"plan": ["hang", "ok"]}})
    env = env_for(path)
    process = start(workspace, env)
    wait_for(lambda: "plan" in stages(calls, path), process)
    run_id = workspace.load_state().run.id

    assert cli(workspace, env, "stop").returncode == 0
    process.wait(30)
    state = workspace.load_state()
    assert state.run.status == RunStatus.stopped
    assert Stage.plan not in state.features[0].done
    assert state.run.stage == Stage.plan

    refused = cli(workspace, env, "resume")
    assert refused.returncode == 1 and "use continue" in refused.stderr

    result = cli(workspace, env, "continue")
    assert result.returncode == 0, result.stderr
    state = workspace.load_state()
    assert state.run.id != run_id
    assert state.features[0].status == FeatureStatus.completed
    assert stages(calls, path) == ["specify", "clarify", "plan", "plan", "tasks", "analyze", "implement", "converge"]


def test_run_and_continue_refused_while_locked(workspace, scenario):
    add_feature(workspace)
    env = env_for(scenario({}))
    workspace.acquire_lock()
    try:
        assert cli(workspace, env, "run").returncode == 3
        assert cli(workspace, env, "continue").returncode == 3
    finally:
        workspace.release_lock()
