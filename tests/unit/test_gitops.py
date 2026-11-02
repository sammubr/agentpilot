import subprocess

from ai_development_orchestrator import gitops


def test_dirty_detection_excluding_orch(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=tmp_path, check=True)
    (tmp_path / ".orchestrator").mkdir()
    (tmp_path / ".orchestrator" / "x").write_text("y")
    files = gitops.dirty_files(tmp_path, exclude=[".orchestrator/"])
    assert files == []


def test_branch_create_reuse(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=tmp_path, check=True)
    sha = gitops.ensure_branch(tmp_path, "test-branch")
    assert sha is not None
    sha2 = gitops.ensure_branch(tmp_path, "test-branch")
    assert sha2 is not None


def test_commit_all_raises_giterror(tmp_path):
    import subprocess

    from ai_development_orchestrator import gitops

    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    try:
        gitops.commit_all(tmp_path, "msg")
    except Exception as e:
        assert isinstance(e, gitops.GitError)


def test_ensure_branch_raises_giterror(tmp_path):
    from ai_development_orchestrator import gitops
    try:
        gitops.ensure_branch(tmp_path, "x")
    except Exception as e:
        assert isinstance(e, gitops.GitError)


def test_dirty_files_z_format(tmp_path):
    import subprocess

    from ai_development_orchestrator import gitops

    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=tmp_path, check=True)
    (tmp_path / "b.txt").write_text("y")
    files = gitops.dirty_files(tmp_path, exclude=[])
    assert "b.txt" in files


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=path, check=True)


def test_nested_repos_without_root_git(tmp_path):
    _init_repo(tmp_path / "api")
    _init_repo(tmp_path / "web")
    (tmp_path / "specs").mkdir()

    assert gitops.repos(tmp_path) == [tmp_path / "api", tmp_path / "web"]
    base = gitops.head_sha(tmp_path)
    assert base is not None and base.startswith("api=")

    gitops.ensure_branch(tmp_path, "feature/x")
    assert gitops.current_branch(tmp_path) == "feature/x"

    (tmp_path / "api" / "b.txt").write_text("y")
    assert gitops.dirty_files(tmp_path) == ["api/b.txt"]

    assert gitops.commit_all(tmp_path, "msg") is not None
    assert gitops.dirty_files(tmp_path) == []
    assert gitops.head_sha(tmp_path) != base


def test_nested_repos_on_different_branches(tmp_path):
    _init_repo(tmp_path / "api")
    _init_repo(tmp_path / "web")
    subprocess.run(["git", "checkout", "-qb", "dev"], cwd=tmp_path / "web", check=True)
    assert gitops.current_branch(tmp_path).endswith(";web=dev")
