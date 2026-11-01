from __future__ import annotations

import json
import os
import subprocess
import sys

from ai_development_orchestrator.models import Feature


def test_subprocess_restart_retries_only_interrupted_step(workspace, scenario, calls):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="survive a process kill"))
    workspace.save_state(state)
    scenario_path = scenario({"claude": {"tasks": "kill_parent"}, "opencode": {}})
    script = (
        "from pathlib import Path; "
        "from ai_development_orchestrator.config import load_config; "
        "from ai_development_orchestrator.engine import Engine; "
        "from ai_development_orchestrator.workspace import Workspace; "
        "w=Workspace(Path.cwd()); Engine(w, load_config(w.config_path)).run()"
    )
    env = os.environ.copy()
    env["FAKE_AGENT_SCENARIO"] = scenario_path
    source = str(__import__("pathlib").Path(__file__).parents[2] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    killed = subprocess.run([sys.executable, "-c", script], cwd=workspace.repo, env=env, check=False)
    assert killed.returncode == -9
    json.loads(workspace.state_path.read_text())
    with open(scenario_path, "w") as scenario_file:
        scenario_file.write('{"claude": {}, "opencode": {}}')
    resumed = subprocess.run([sys.executable, "-c", script], cwd=workspace.repo, env=env, check=False)
    assert resumed.returncode == 0
    state = workspace.load_state()
    assert state.features[0].status.value == "completed"
    steps = [record["step"] for record in calls(scenario_path)]
    for step in ("select", "specify", "clarify", "plan"):
        assert steps.count(step) == 1
    assert steps.count("tasks") == 2
    json.loads(workspace.state_path.read_text())
