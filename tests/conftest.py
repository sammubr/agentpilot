from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture
def target_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    specify = repo / ".specify"
    specify.mkdir(parents=True)
    (specify / ".gitignore").write_text("feature.json\n")
    skills = repo / ".claude" / "skills" / "speckit-specify"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("x")
    for name in ["speckit-clarify", "speckit-plan", "speckit-tasks", "speckit-analyze", "speckit-converge"]:
        s = repo / ".claude" / "skills" / name
        s.mkdir(parents=True)
        (s / "SKILL.md").write_text("x")
    commands = repo / ".opencode" / "commands"
    commands.mkdir(parents=True)
    (commands / "speckit.implement.md").write_text("x")
    (repo / "specs").mkdir(exist_ok=True)
    subprocess.run(["git", "add", ".specify", ".claude", ".opencode"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add-specify"], cwd=repo, check=True)
    return repo


@pytest.fixture
def workspace(target_repo):
    from ai_development_orchestrator.config import DEFAULT_CONFIG_TOML
    from ai_development_orchestrator.workspace import Workspace

    ws = Workspace(target_repo)
    ws.init()
    cfg_path = ws.config_path
    cfg = DEFAULT_CONFIG_TOML
    cfg = cfg.replace('command = ["claude"]', f'command = [{sys.executable!r}, "{Path(__file__).parent / "fakes" / "fake_claude.py"}"]')
    cfg = cfg.replace('command = ["opencode"]', f'command = [{sys.executable!r}, "{Path(__file__).parent / "fakes" / "fake_opencode.py"}"]')
    cfg = cfg.replace('timeout_s = 1800', 'timeout_s = 10')
    # set fast retry delays
    cfg = cfg.replace('initial_delay_s = 30', 'initial_delay_s = 0.01')
    cfg = cfg.replace('usage_limit_initial_delay_s = 300', 'usage_limit_initial_delay_s = 0.01')
    cfg = cfg.replace('usage_limit_margin_s = 60', 'usage_limit_margin_s = 0')
    cfg = cfg.replace('max_delay_s = 1800', 'max_delay_s = 0.05')
    cfg_path.write_text(cfg)
    return ws


@pytest.fixture
def scenario(tmp_path):
    def _make(data):
        p = tmp_path / "scenario.json"
        p.write_text(json.dumps(data))
        return str(p)

    return _make


@pytest.fixture
def calls():
    def _read(scenario_path):
        log = Path(scenario_path + ".calls.jsonl")
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    return _read


@pytest.fixture
def drive():
    """Run an Engine in a thread; answer each pause with the next control ("resume" or "stop")."""

    def _drive(engine, controls=("stop",), timeout=60, **kwargs):
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("status", engine.run(**kwargs)), daemon=True)
        thread.start()
        pending = list(controls)
        deadline = time.monotonic() + timeout
        while thread.is_alive() and time.monotonic() < deadline:
            state = engine.ws.load_state() if engine.ws.state_path.exists() else None
            if state and state.run.status == "paused" and pending and engine.ws.read_control() is None:
                control = pending.pop(0)
                engine.ws.request(control() if callable(control) else control)
            time.sleep(0.05)
        thread.join(1)
        assert not thread.is_alive(), "engine did not finish"
        return result["status"]

    return _drive


@pytest.fixture
def no_sleep(monkeypatch):
    import time

    def fake_sleep(s):
        return

    monkeypatch.setattr(time, "sleep", fake_sleep)
    return True
