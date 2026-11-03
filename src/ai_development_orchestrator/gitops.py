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


def _repo_default_branch(repo: Path) -> str:
    origin_head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if origin_head.returncode == 0:
        return _prefer_local_default(repo, origin_head.stdout.strip())

    remotes = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes")
    heads = [ref for ref in remotes.stdout.splitlines() if ref.endswith("/HEAD")]
    branches = {
        _prefer_local_default(repo, result.stdout.strip())
        for ref in heads
        if (result := _git(repo, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{ref}")).returncode == 0
    }
    if len(branches) == 1:
        return branches.pop()
    if len(branches) > 1:
        raise GitError(f"multiple remote default branches are configured in {repo}")
    local_refs = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    local_branches = local_refs.stdout.splitlines()
    conventional = [branch for branch in local_branches if branch in {"main", "master"}]
    if len(conventional) == 1:
        return conventional[0]
    if len(local_branches) == 1:
        return local_branches[0]
    raise GitError(
        f"could not resolve the repository default branch in {repo}; configure a remote HEAD"
    )


def _prefer_local_default(repo: Path, remote_branch: str) -> str:
    _, separator, local_branch = remote_branch.partition("/")
    if separator and branch_exists(repo, local_branch):
        return local_branch
    return remote_branch


def default_branch(root: Path) -> str:
    found = repos(root)
    if not found:
        raise GitError(f"no Git repository found in {root}")
    return _combine(root, {repo: _repo_default_branch(repo) for repo in found}) or ""


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


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    command = ["git", "merge-base", "--is-ancestor", ancestor, descendant]
    result = _run(command, repo)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitError(f"could not verify branch ancestry in {repo}", command, result.stderr)


def ensure_branch(root: Path, name: str, start_point: str | None = None) -> str | None:
    found = repos(root) or [root]
    targets = _split(root, name)
    start_points = _split(root, start_point)
    for repo in found:
        target = targets.get(repo, name)
        parent = start_points.get(repo, start_point)
        if branch_exists(repo, target):
            if parent and not _is_ancestor(repo, parent, target):
                raise GitError(f"branch {target} is not based on expected branch {parent} in {repo}")
            _check(repo, "checkout", target)
        else:
            command = ["checkout", "-b", target]
            if parent:
                command.append(parent)
            _check(repo, *command)
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
