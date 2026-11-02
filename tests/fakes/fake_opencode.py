#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

from fake_common import failure_text, pick_behavior, side_effect, spec_dir


def implement(behavior: str) -> None:
    tasks = spec_dir() / "tasks.md"
    text = tasks.read_text()
    if behavior in ("partial", "partial_kill"):
        text = text.replace("- [ ]", "- [X]", 1)
    elif behavior != "no_changes":
        text = text.replace("- [ ]", "- [X]")
    tasks.write_text(text)
    if behavior != "no_changes":
        out = Path("src_out")
        out.mkdir(exist_ok=True)
        (out / f"{spec_dir().name}-{len(list(out.iterdir()))}.txt").write_text("code")


def main() -> None:
    if "--version" in sys.argv:
        print("fake-opencode 1.0")
        return
    message = sys.argv[-1]
    feature = spec_dir().name.split("-", 1)[-1]
    behavior = pick_behavior("opencode", "implement", feature, message)
    failure = side_effect(behavior)
    if failure == "crash":
        sys.exit(1)
    if failure == "invalid":
        print("not json")
        return
    print(json.dumps({"type": "step_start", "sessionID": "sess-opencode"}))
    if failure:
        print(json.dumps({"type": "error", "error": {"data": {"message": failure_text(failure)}}}))
        sys.exit(1)
    implement(behavior)
    if behavior == "partial_kill":
        os.kill(os.getppid(), 9)
    print(json.dumps({"type": "text", "part": {"text": "Implemented."}}))


if __name__ == "__main__":
    main()
