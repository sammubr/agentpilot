#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path


def main():
    if "--version" in sys.argv:
        print("fake-opencode 1.0")
        return
    scenario_path = os.environ.get("FAKE_AGENT_SCENARIO")
    if not scenario_path:
        return
    try:
        scenario = json.loads(Path(scenario_path).read_text())
    except Exception:
        scenario = {}
    opencode_cfg = scenario.get("opencode", {})
    calls_log = scenario_path + ".calls.jsonl"

    prompt = sys.argv[-1] if sys.argv else ""
    # also check -p style if any
    for i, arg in enumerate(sys.argv):
        if arg == "-p" and i + 1 < len(sys.argv):
            prompt = sys.argv[i + 1]
            break
    step = None
    if "[orchestrator" in prompt:
        try:
            for part in prompt.split():
                if part.startswith("step="):
                    step = part.split("=", 1)[1]
                    break
        except Exception:
            pass
    feature = None
    if "[orchestrator" in prompt:
        try:
            for part in prompt.split():
                if part.startswith("feature="):
                    feature = part.split("=", 1)[1]
                    break
        except Exception:
            pass

    behaviors = []
    if step and step in opencode_cfg:
        b = opencode_cfg[step]
        if isinstance(b, list):
            behaviors = b
        else:
            behaviors = [b]
    behavior = behaviors[0] if behaviors else "ok"
    try:
        cnt = 0
        if os.path.exists(calls_log):
            with open(calls_log) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("agent") == "opencode" and rec.get("step") == step:
                            cnt += 1
                    except Exception:
                        pass
        if len(behaviors) > cnt:
            behavior = behaviors[cnt]
        elif behaviors:
            behavior = behaviors[-1]
    except Exception:
        pass

    with open(calls_log, "a") as f:
        f.write(json.dumps({"agent": "opencode", "step": step, "feature": feature, "behavior": behavior, "pid": os.getpid(), "argv": sys.argv, "opencode_config_content": os.environ.get("OPENCODE_CONFIG_CONTENT")}) + "\n")

    repo = os.getcwd()
    orch = Path(repo) / ".orchestrator"
    control = orch / "control"

    # emit session start
    print(json.dumps({"type": "step_start", "sessionID": "sess-op1"}))
    print(json.dumps({"type": "text", "part": {"text": "starting"}}))

    if behavior == "usage_limit":
        print(json.dumps({"type": "error", "error": {"data": {"message": "usage limit exceeded"}}}))
        sys.exit(1)
    if behavior == "usage_limit_reset":
        print(json.dumps({"type": "error", "error": {"data": {"message": "usage limit exceeded | " + str(int(time.time()) + 1)}}}))
        sys.exit(1)
    if behavior == "auth":
        print(json.dumps({"type": "error", "error": {"data": {"message": "unauthorized"}}}))
        sys.exit(1)
    if behavior == "network":
        print(json.dumps({"type": "error", "error": {"data": {"message": "network error"}}}))
        sys.exit(1)
    if behavior == "crash":
        sys.exit(1)
    if behavior == "invalid":
        print("not json")
        sys.exit(0)  # might be treated as ok or bad depending
    if behavior == "hang":
        time.sleep(600)
        return
    if behavior == "request_pause":
        try:
            orch.mkdir(parents=True, exist_ok=True)
            control.write_text("pause")
        except Exception:
            pass
    if behavior == "kill_parent":
        try:
            os.kill(os.getppid(), 9)
        except Exception:
            pass
        # fall through
    if behavior == "break":
        try:
            (Path(repo) / "BROKEN").write_text("broken")
        except Exception:
            pass
        print(json.dumps({"type": "text", "part": {"text": "done"}}))
        sys.exit(0)
    if behavior == "no_changes":
        print(json.dumps({"type": "text", "part": {"text": "no changes made"}}))
        sys.exit(0)
        return

    # ok
    try:
        src = Path(repo) / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / f"{feature or 'out'}.txt").write_text(f"ok {step}")
        broken = Path(repo) / "BROKEN"
        if broken.exists():
            broken.unlink()
    except Exception:
        pass
    print(json.dumps({"type": "text", "part": {"text": "Implemented changes. Tests passed."}}))
    sys.exit(0)


if __name__ == "__main__":
    main()
