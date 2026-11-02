"""Shared logic for the fake Claude Code and OpenCode executables used in tests.

Scenario JSON (path in FAKE_AGENT_SCENARIO): {"claude": {...}, "opencode": {...}} mapping a key to a
behavior or a list of behaviors (one per call; the last repeats). Keys are tried in this order:
"<stage>:<feature>", "<stage>". The feature is the spec directory name without its number prefix
(the title's first word, lower-cased, for specify).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CONTROL = Path(".orchestrator") / "control"


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.split(":")[0].split()[0].lower()).strip("-") or "feature"


def feature_key(stage: str, message: str) -> str:
    if stage == "specify":
        return slug(message.removeprefix("/speckit-specify "))
    spec_dir = os.environ.get("SPECIFY_FEATURE_DIRECTORY", "")
    return Path(spec_dir).name.split("-", 1)[-1]


def pick_behavior(agent: str, stage: str, feature: str, message: str) -> str:
    path = os.environ.get("FAKE_AGENT_SCENARIO")
    scenario = json.loads(Path(path).read_text()) if path else {}
    rules = scenario.get(agent, {})
    rule = rules.get(f"{stage}:{feature}", rules.get(stage, "ok"))
    behaviors = rule if isinstance(rule, list) else [rule]
    log = Path(path + ".calls.jsonl") if path else None
    previous = 0
    if log and log.exists():
        records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        previous = sum(1 for r in records if r["agent"] == agent and r["stage"] == stage and r["feature"] == feature)
    behavior = behaviors[min(previous, len(behaviors) - 1)]
    if log:
        record = {
            "agent": agent, "stage": stage, "feature": feature, "behavior": behavior, "message": message,
            "spec_dir": os.environ.get("SPECIFY_FEATURE_DIRECTORY"), "argv": sys.argv, "pid": os.getpid(),
            "opencode_config_content": os.environ.get("OPENCODE_CONFIG_CONTENT"),
        }
        with log.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
    return behavior


def side_effect(behavior: str) -> str | None:
    """Run behaviors that act before the normal result; return a failure kind to report, if any."""
    if behavior.startswith("sleep:"):
        time.sleep(float(behavior.split(":")[1]))
    elif behavior == "hang":
        time.sleep(600)
    elif behavior == "request_pause":
        CONTROL.write_text("pause")
    elif behavior == "switch_branch":
        subprocess.run(["git", "checkout", "-q", "-b", f"stray-{time.time_ns()}"], check=False)
    elif behavior == "commit":
        Path("agent_commit.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], check=False)
        subprocess.run(["git", "commit", "-q", "-m", "agent commit"], check=False)
    elif behavior == "kill_parent":
        os.kill(os.getppid(), 9)
        time.sleep(5)
    elif behavior in ("usage_limit", "usage_limit_reset", "auth", "network", "crash", "invalid"):
        return behavior
    return None


def failure_text(kind: str) -> str:
    return {
        "usage_limit": "usage limit exceeded",
        "usage_limit_reset": f"usage limit exceeded | {int(time.time()) + 2}",
        "auth": "unauthorized: invalid api key",
        "network": "network error: connection failed",
    }.get(kind, "error")


def spec_dir() -> Path:
    return Path(os.environ["SPECIFY_FEATURE_DIRECTORY"])
