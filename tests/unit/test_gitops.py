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
