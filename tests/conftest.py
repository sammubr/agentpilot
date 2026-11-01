from __future__ import annotations

import json
import os
import subprocess
import sys
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
    (repo / "specs").mkdir(exist_ok=True)
    # commit .specify/.gitignore and .claude/skills/* files
    subprocess.run(["git", "add", ".specify", ".claude"], cwd=repo, check=True)
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
    # validation commands
    if '[validation]' in cfg:
        # replace commands line
        cfg = cfg.replace('commands = []', 'commands = ["test ! -f BROKEN"]')
    else:
        cfg += '\n[validation]\ncommands = ["test ! -f BROKEN"]\n'
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
        calls_log = scenario_path + ".calls.jsonl"
        res = []
        if os.path.exists(calls_log):
            with open(calls_log) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        res.append(json.loads(line))
                    except Exception:
                        pass
        return res

    return _read


@pytest.fixture
def no_sleep(monkeypatch):
    import time

    def fake_sleep(s):
        return

    monkeypatch.setattr(time, "sleep", fake_sleep)
    return True
