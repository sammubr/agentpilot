from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    def __init__(self, message: str, command: list[str] | None = None, stderr: str | None = None):
        super().__init__(message)
        self.command = command
        self.stderr = stderr


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def is_repo(cwd: Path) -> bool:
    r = _run(["git", "rev-parse", "--git-dir"], cwd)
    return r.returncode == 0


def current_branch(cwd: Path) -> str | None:
    r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def head_sha(cwd: Path) -> str | None:
    r = _run(["git", "rev-parse", "HEAD"], cwd)
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def dirty_files(cwd: Path, exclude: list[str] | None = None) -> list[str]:
    exclude = exclude or [".orchestrator/"]
    r = _run(["git", "status", "--porcelain", "-z"], cwd)
    if r.returncode != 0:
        return []
    files = []
    # split by null
    parts = r.stdout.split("\0")
    i = 0
    while i < len(parts):
        line = parts[i]
        i += 1
        if not line.strip():
            continue
        if len(line) < 3:
            continue
        # porcelain -z: first two chars status, third space, then path; for rename there might be orig path followed by new path
        status = line[:2]
        path = line[3:]
        if "R" in status or "C" in status and i < len(parts):
            new_path = parts[i]
            i += 1
            path = new_path
        skip = False
        for ex in exclude:
            if path.startswith(ex) or ex in path:
                skip = True
                break
        if skip:
            continue
        files.append(path)
    return files


def branch_exists(cwd: Path, name: str) -> bool:
    r = _run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"], cwd)
    return r.returncode == 0


def ensure_branch(cwd: Path, name: str) -> str | None:
    if branch_exists(cwd, name):
        r = _run(["git", "checkout", name], cwd)
        if r.returncode != 0:
            raise GitError("git checkout failed", ["git", "checkout", name], r.stderr)
        return head_sha(cwd)
    r = _run(["git", "checkout", "-b", name], cwd)
    if r.returncode != 0:
        raise GitError("git checkout -b failed", ["git", "checkout", "-b", name], r.stderr)
    return head_sha(cwd)


def commit_all(cwd: Path, message: str) -> str | None:
    r = _run(["git", "add", "-A"], cwd)
    if r.returncode != 0:
        raise GitError("git add failed", ["git", "add", "-A"], r.stderr)
    r2 = _run(["git", "diff", "--cached", "--quiet"], cwd)
    if r2.returncode == 0:
        return None
    r3 = _run(["git", "commit", "-m", message], cwd)
    if r3.returncode != 0:
        raise GitError("git commit failed", ["git", "commit", "-m", message], r3.stderr)
    return head_sha(cwd)


def diff(cwd: Path, base: str) -> str:
    if not base:
        r = _run(["git", "diff"], cwd)
    else:
        r = _run(["git", "diff", base, "HEAD"], cwd)
    return r.stdout


def changed_files(cwd: Path, base: str) -> list[str]:
    if not base:
        return []
    r = _run(["git", "diff", "--name-only", base, "HEAD"], cwd)
    if r.returncode != 0:
        return []
    return [l for l in r.stdout.splitlines() if l.strip()]


def push(cwd: Path, remote: str, branch: str) -> bool:
    r = _run(["git", "push", "-u", remote, branch], cwd)
    return r.returncode == 0
