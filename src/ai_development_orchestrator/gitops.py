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


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd)


def _check(cwd: Path, *args: str) -> None:
    r = _git(cwd, *args)
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed in {cwd}", ["git", *args], r.stderr)


def is_repo(cwd: Path) -> bool:
    return _git(cwd, "rev-parse", "--git-dir").returncode == 0


def repos(root: Path) -> list[Path]:
    if is_repo(root):
        return [root]
    return sorted(path.parent for path in root.glob("*/.git"))


def _prefix(root: Path, repo: Path) -> str:
    return "" if repo == root else repo.relative_to(root).as_posix() + "/"


def _combine(root: Path, values: dict[Path, str]) -> str | None:
    if not values:
        return None
    if list(values) == [root]:
        return values[root]
    return ";".join(f"{_prefix(root, repo).rstrip('/')}={value}" for repo, value in values.items())


def _split(root: Path, combined: str | None) -> dict[Path, str]:
    if not combined:
        return {}
    if "=" not in combined:
        return {repo: combined for repo in repos(root)}
    pairs = (item.split("=", 1) for item in combined.split(";"))
    return {root / name: value for name, value in pairs}


def _repo_branch(repo: Path) -> str | None:
    r = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def _repo_head(repo: Path) -> str | None:
    r = _git(repo, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def current_branch(root: Path) -> str | None:
    branches = {repo: branch for repo in repos(root) if (branch := _repo_branch(repo))}
    if len(set(branches.values())) == 1:
        return next(iter(branches.values()))
    return _combine(root, branches)


def head_sha(root: Path) -> str | None:
    return _combine(root, {repo: sha for repo in repos(root) if (sha := _repo_head(repo))})


def _repo_dirty(repo: Path) -> list[str]:
    r = _git(repo, "status", "--porcelain", "-z")
    if r.returncode != 0:
        return []
    files = []
    parts = r.stdout.split("\0")
    i = 0
    while i < len(parts):
        line = parts[i]
        i += 1
        if len(line) < 3:
            continue
        status = line[:2]
        path = line[3:]
        if ("R" in status or "C" in status) and i < len(parts):
            i += 1
        files.append(path)
    return files


def dirty_files(root: Path, exclude: list[str] | None = None) -> list[str]:
    exclude = exclude or [".orchestrator/"]
    files = [_prefix(root, repo) + path for repo in repos(root) for path in _repo_dirty(repo)]
    return [path for path in files if not any(path.startswith(ex) or ex in path for ex in exclude)]


def branch_exists(cwd: Path, name: str) -> bool:
    return _git(cwd, "show-ref", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0


def ensure_branch(root: Path, name: str) -> str | None:
    found = repos(root) or [root]
    for repo in found:
        if branch_exists(repo, name):
            _check(repo, "checkout", name)
        else:
            _check(repo, "checkout", "-b", name)
    return head_sha(root)


def commit_all(root: Path, message: str) -> str | None:
    committed = False
    for repo in repos(root) or [root]:
        _check(repo, "add", "-A")
        if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
            continue
        _check(repo, "commit", "-m", message)
        committed = True
    return head_sha(root) if committed else None


def push(root: Path, remote: str, branch: str) -> bool:
    branches = _split(root, branch)
    results = [_git(repo, "push", "-u", remote, branches.get(repo, branch)).returncode == 0 for repo in repos(root)]
    return bool(results) and all(results)
