from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ai_development_orchestrator.models import Feature


def _environment(workspace, scenario_path: str) -> dict[str, str]:
    env = os.environ.copy()
    env["FAKE_AGENT_SCENARIO"] = scenario_path
    source = str(Path(__file__).parents[2] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _cli(workspace, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ai_development_orchestrator", "--repo", str(workspace.repo), *args],
        cwd=workspace.repo, env=env, capture_output=True, text=True, check=False,
    )


def _wait_for(workspace, predicate, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"orchestrator exited early: {process.returncode}\n{stdout}\n{stderr}")
        time.sleep(0.05)
    raise AssertionError("orchestrator did not reach the expected state")


def _start(workspace, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "ai_development_orchestrator", "--repo", str(workspace.repo), "run"],
        cwd=workspace.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _read_calls(path: str) -> list[dict]:
    calls_path = Path(path + ".calls.jsonl")
    if not calls_path.exists():
        return []
    return [json.loads(line) for line in calls_path.read_text().splitlines()]


def _has_call(path: str, step: str, behavior: str) -> bool:
    return any(item.get("step") == step and item.get("behavior") == behavior for item in _read_calls(path))


def test_pause_usage_limit_wait_and_resume(workspace, scenario):
    workspace.config_path.write_text(workspace.config_path.read_text().replace(
        "usage_limit_initial_delay_s = 0.01", "usage_limit_initial_delay_s = 30"
    ))
    workspace.save_state(workspace.load_state().model_copy(update={
        "features": [Feature(id="F1", title="Feature", description="pause during recovery")]
    }))
    path = scenario({"claude": {"plan": ["usage_limit", "ok"]}})
    env = _environment(workspace, path)
    process = _start(workspace, env)
    try:
        _wait_for(workspace, lambda: workspace.load_state().run.status.value == "waiting", process)
        paused = _cli(workspace, env, "pause")
        assert paused.returncode == 0, paused.stderr
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert workspace.load_state().run.status.value == "paused"
        resumed = _cli(workspace, env, "resume")
        assert resumed.returncode == 0, resumed.stderr
        assert workspace.load_state().features[0].status.value == "completed"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)


def test_stop_hang_kills_agent_group_and_resume(workspace, scenario):
    workspace.save_state(workspace.load_state().model_copy(update={
        "features": [Feature(id="F1", title="Feature", description="stop a hung agent")]
    }))
    path = scenario({"claude": {"select": "hang"}})
    env = _environment(workspace, path)
    process = _start(workspace, env)
    try:
        _wait_for(workspace, lambda: _has_call(path, "select", "hang"), process)
        call = next(item for item in _read_calls(path) if item.get("step") == "select")
        assert call["step"] == "select" and call["behavior"] == "hang"
        stopped = _cli(workspace, env, "stop")
        assert stopped.returncode == 0, stopped.stderr
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert workspace.load_state().run.status.value == "stopped"
        records = _read_calls(path)
        fake_pid = next(record["pid"] for record in records if record.get("behavior") == "hang")
        try:
            os.killpg(fake_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("agent process group is still alive")
        with open(path, "w") as scenario_file:
            scenario_file.write("{}")
        resumed = _cli(workspace, env, "resume")
        assert resumed.returncode == 0, resumed.stderr
        assert workspace.load_state().features[0].status.value == "completed"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)
