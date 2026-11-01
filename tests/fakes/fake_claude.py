#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path


def main():
    if "--version" in sys.argv:
        print("fake-claude 1.0")
        return
    scenario_path = os.environ.get("FAKE_AGENT_SCENARIO")
    if not scenario_path:
        return
    try:
        scenario = json.loads(Path(scenario_path).read_text())
    except Exception:
        scenario = {}
    claude_cfg = scenario.get("claude", {})
    calls_log = scenario_path + ".calls.jsonl"

    # find step from args/prompt
    prompt = ""
    for i, arg in enumerate(sys.argv):
        if arg == "-p" and i + 1 < len(sys.argv):
            prompt = sys.argv[i + 1]
            break
    step = None
    if "[orchestrator" in prompt:
        try:
            # extract step=...
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
    behavior_key = f"{step}:{feature}" if step and feature and f"{step}:{feature}" in claude_cfg else step
    if behavior_key not in claude_cfg and step and step.startswith("converge.") and "converge" in claude_cfg:
        behavior_key = "converge"
    if behavior_key and behavior_key in claude_cfg:
        b = claude_cfg[behavior_key]
        if isinstance(b, list):
            behaviors = b
        else:
            behaviors = [b]
    behavior = behaviors[0] if behaviors else "ok"
    # track call count
    try:
        cnt = 0
        if os.path.exists(calls_log):
            with open(calls_log) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        matches_step = rec.get("step") == step
                        if behavior_key == "converge":
                            matches_step = bool(rec.get("step", "").startswith("converge."))
                        if rec.get("agent") == "claude" and matches_step and rec.get("feature") == feature:
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
        f.write(json.dumps({"agent": "claude", "step": step, "feature": feature, "behavior": behavior, "pid": os.getpid(), "argv": sys.argv}) + "\n")

    repo = os.getcwd()
    orch = Path(repo) / ".orchestrator"
    control = orch / "control"

    if behavior == "usage_limit":
        print(json.dumps({"type": "result", "is_error": True, "result": "usage limit exceeded", "session_id": "sess1"}))
        return
    if behavior == "usage_limit_reset":
        print(json.dumps({"type": "result", "is_error": True, "result": "usage limit exceeded | " + str(int(time.time()) + 1), "session_id": "sess1"}))
        return
    if behavior == "auth":
        print(json.dumps({"type": "result", "is_error": True, "result": "unauthorized: invalid api key", "session_id": "sess1"}))
        return
    if behavior == "network":
        print(json.dumps({"type": "result", "is_error": True, "result": "network error: connection failed", "session_id": "sess1"}))
        return
    if behavior == "crash":
        sys.exit(1)
    if behavior == "invalid":
        print("not json at all")
        sys.exit(0)
    if behavior == "hang":
        time.sleep(600)
        return
    if behavior == "request_pause":
        try:
            orch.mkdir(parents=True, exist_ok=True)
            control.write_text("pause")
        except Exception:
            pass
        # continue as ok
    if behavior == "switch_branch":
        try:
            import subprocess

            subprocess.run(["git", "checkout", "-b", f"stray-{int(time.time())}"], cwd=repo, check=False)
        except Exception:
            pass
        # continue as ok
    if behavior == "kill_parent":
        try:
            os.kill(os.getppid(), 9)
        except Exception:
            pass
        # fall through

    # check for dep scenario behavior
    dep_behavior = behavior
    if isinstance(dep_behavior, str) and dep_behavior.startswith("dep:"):
        dep_id = dep_behavior.split(":", 1)[1]
        res_text = f'{{"dependencies": [{{"id": "{dep_id}", "rationale": "fake"}}]}}'
        print(json.dumps({"type": "result", "is_error": False, "result": res_text, "session_id": "sess1"}))
        return

    # ok behavior - write artifacts based on step
    if step == "select" and feature:
        print(json.dumps({"type": "result", "is_error": False, "result": '{"dependencies": []}', "session_id": "sess1"}))
        return
    if step == "specify" and feature:
        specs = Path(repo) / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        n = 1
        for p in specs.iterdir():
            try:
                name = p.name
                if p.is_dir() and "-" in name:
                    nn = int(name.split("-")[0])
                    if nn >= n:
                        n = nn + 1
            except (ValueError, IndexError):
                pass
        feature_slug = str(feature).lower().replace(" ", "-")
        spec_dir_name = f"{n:03d}-{feature_slug}"
        spec_dir = specs / spec_dir_name
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "spec.md").write_text(f"# Spec for {feature}\n")
        (spec_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
        specify_json = Path(repo) / ".specify" / "feature.json"
        specify_json.parent.mkdir(parents=True, exist_ok=True)
        specify_json.write_text(json.dumps({"feature_directory": f"specs/{spec_dir_name}"}))
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return
    if step in ("clarify", "plan", "tasks"):
        spec_dir = None
        for part in prompt.split():
            if part.startswith("spec_dir=") and part != "spec_dir=-":
                spec_dir = part.split("=", 1)[1]
                break
        if not spec_dir:
            # try to read from feature.json
            try:
                pj = Path(repo) / ".specify" / "feature.json"
                if pj.exists():
                    d = json.loads(pj.read_text())
                    spec_dir = d.get("feature_directory")
            except (OSError, ValueError):
                pass
        if spec_dir:
            sd = Path(repo) / spec_dir
            sd.mkdir(parents=True, exist_ok=True)
            if step == "clarify":
                if not (sd / "spec.md").exists():
                    (sd / "spec.md").write_text("# Spec\n")
            elif step == "plan":
                if not (sd / "plan.md").exists():
                    (sd / "plan.md").write_text("# Plan\n")
            elif step == "tasks" and not (sd / "tasks.md").exists():
                (sd / "tasks.md").write_text("# Tasks\n")
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return
    if step == "analyze":
        spec_dir = None
        for part in prompt.split():
            if part.startswith("spec_dir=") and part != "spec_dir=-":
                spec_dir = part.split("=", 1)[1]
                break
        if spec_dir:
            sd = Path(repo) / spec_dir / "orchestrator"
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "analysis.md").write_text("analysis ok\n")
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return
    if step == "implement.handoff":
        spec_dir = None
        for part in prompt.split():
            if part.startswith("spec_dir=") and part != "spec_dir=-":
                spec_dir = part.split("=", 1)[1]
                break
        if spec_dir:
            sd = Path(repo) / spec_dir / "orchestrator"
            sd.mkdir(parents=True, exist_ok=True)
            handoff = """## Feature
F1

## Specification
spec.md

## Plan
plan.md

## Tasks
tasks.md

## Project Instructions
see AGENTS.md

## Architecture Decisions
simple

## Acceptance Criteria
works

## Validations
tests

## Restrictions
none
"""
            if behavior == "bad_handoff":
                handoff = handoff.replace("\n## Restrictions\nnone\n", "\n")
            (sd / "handoff-implement.md").write_text(handoff)
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return
    if step and step.startswith("converge"):
        # check if reject behavior for this call
        # look up converge behavior
        conv_behaviors = claude_cfg.get(step) or claude_cfg.get("converge") or []
        if isinstance(conv_behaviors, list) and conv_behaviors:
            # determine which call
            pass
        # see if any reject
        b = behavior
        if b == "reject":
            spec_dir = None
            for part in prompt.split():
                if part.startswith("spec_dir=") and part != "spec_dir=-":
                    spec_dir = part.split("=", 1)[1]
                    break
            if spec_dir:
                sd = Path(repo) / spec_dir / "orchestrator"
                sd.mkdir(parents=True, exist_ok=True)
                verdict = {
                    "approved": False,
                    "summary": "needs changes",
                    "findings": [{"severity": "major", "description": "fix needed", "location": None}],
                    "acceptance_criteria": [],
                }
                # extract round
                r = int(step.rsplit(".", 1)[1])
                (sd / f"verdict-{r}.json").write_text(json.dumps(verdict))
        else:
            spec_dir = None
            for part in prompt.split():
                if part.startswith("spec_dir=") and part != "spec_dir=-":
                    spec_dir = part.split("=", 1)[1]
                    break
            if spec_dir:
                sd = Path(repo) / spec_dir / "orchestrator"
                sd.mkdir(parents=True, exist_ok=True)
                r = int(step.rsplit(".", 1)[1])
                verdict = {
                    "approved": True,
                    "summary": "ok",
                    "findings": [],
                    "acceptance_criteria": [{"criterion": "ok", "met": True, "evidence": "ok"}],
                }
                (sd / f"verdict-{r}.json").write_text(json.dumps(verdict))
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return
    if step and step.startswith("correct.handoff"):
        spec_dir = None
        for part in prompt.split():
            if part.startswith("spec_dir=") and part != "spec_dir=-":
                spec_dir = part.split("=", 1)[1]
                break
        if spec_dir:
            sd = Path(repo) / spec_dir / "orchestrator"
            sd.mkdir(parents=True, exist_ok=True)
            r = int(step.rsplit(".", 1)[1])
            handoff = """## Feature
F1

## Specification
spec.md

## Plan
plan.md

## Tasks
tasks.md

## Project Instructions
see AGENTS.md

## Architecture Decisions
simple

## Acceptance Criteria
works

## Validations
tests

## Restrictions
none

## Findings To Fix
fix it
"""
            (sd / f"handoff-correction-{r}.md").write_text(handoff)
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return
    if step == "post_analyze":
        spec_dir = None
        for part in prompt.split():
            if part.startswith("spec_dir=") and part != "spec_dir=-":
                spec_dir = part.split("=", 1)[1]
                break
        if spec_dir:
            sd = Path(repo) / spec_dir / "orchestrator"
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "post-analysis.md").write_text("post ok\n")
        print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
        return

    print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "sess1"}))
    return


if __name__ == "__main__":
    main()
