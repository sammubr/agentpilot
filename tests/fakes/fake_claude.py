#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from fake_common import failure_text, feature_key, pick_behavior, side_effect, slug, spec_dir


def result(text: str, error: bool = False) -> None:
    print(json.dumps({"type": "result", "is_error": error, "result": text, "session_id": "sess-claude"}))


def specify(message: str) -> None:
    title = message.removeprefix("/speckit-specify ").split("\n")[0]
    specs = Path("specs")
    number = len([p for p in specs.iterdir() if p.is_dir()]) + 1 if specs.exists() else 1
    directory = specs / f"{number:03d}-{slug(title)}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.md").write_text(f"# {title}\n")
    Path(".specify").mkdir(exist_ok=True)
    Path(".specify/feature.json").write_text(json.dumps({"feature_directory": directory.as_posix()}))


def converge(behavior: str) -> None:
    if behavior == "append":
        tasks = spec_dir() / "tasks.md"
        count = tasks.read_text().count("- [")
        tasks.write_text(tasks.read_text() + f"\n## Phase N: Convergence\n\n- [ ] T9{count:02d} Fix gap\n")


def main() -> None:
    if "--version" in sys.argv:
        print("fake-claude 1.0")
        return
    message = sys.argv[sys.argv.index("-p") + 1]
    stage = message.split()[0].removeprefix("/speckit-")
    behavior = pick_behavior("claude", stage, feature_key(stage, message), message)
    failure = side_effect(behavior)
    if failure == "crash":
        sys.exit(1)
    if failure == "invalid":
        print("not json at all")
        return
    if failure:
        result(failure_text(failure), error=True)
        return
    if behavior != "no_artifact":
        if stage == "specify":
            specify(message)
        elif stage == "plan":
            (spec_dir() / "plan.md").write_text("# Plan\n")
        elif stage == "tasks":
            (spec_dir() / "tasks.md").write_text("# Tasks\n\n- [ ] T001 First\n- [ ] T002 Second\n")
        elif stage == "converge":
            converge(behavior)
    result(f"{stage} done")


if __name__ == "__main__":
    main()
