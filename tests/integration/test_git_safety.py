from __future__ import annotations

import json
import subprocess

import pytest

from ai_development_orchestrator import gitops
from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine, StartRefused
from ai_development_orchestrator.models import Feature, FeatureStatus, RunStatus


def _add_feature(workspace) -> None:
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="Git safety coverage"))
    workspace.save_state(state)


def _git(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.mark.parametrize("dirty_kind", ["untracked", "modified"])
def test_dirty_start_refuses_without_modifying_file_branch_or_state(workspace, dirty_kind):
    _add_feature(workspace)
    if dirty_kind == "modified":
        dirty_file = workspace.repo / "README.md"
        expected_content = "operator changes\n"
    else:
        dirty_file = workspace.repo / "operator.txt"
        expected_content = "operator changes\n"
    dirty_file.write_text(expected_content)
    before_state = workspace.state_path.read_bytes()
    before_branch = gitops.current_branch(workspace.repo)
    config = load_config(workspace.config_path)

    with pytest.raises(StartRefused, match=f"working tree has uncommitted changes: {dirty_file.name}"):
        Engine(workspace, config, handle_signals=False).run()

    assert dirty_file.read_text() == expected_content
    assert gitops.current_branch(workspace.repo) == before_branch
    assert workspace.state_path.read_bytes() == before_state


def test_paused_run_reports_unexpected_file_and_head_move(workspace, scenario, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"plan": "request_pause"}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)

    assert Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run() == RunStatus.paused
    assert "plan" in workspace.load_state().features[0].completed_steps

    stray = workspace.repo / "stray.txt"
    stray.write_text("unexpected\n")
    assert Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run() == RunStatus.paused
    state = workspace.load_state()
    assert "unexpected_changes" in state.run.status_reason
    assert "stray.txt" in state.run.status_reason

    stray.unlink()
    (workspace.repo / "README.md").write_text("new committed revision\n")
    _git(workspace.repo, "add", "README.md")
    _git(workspace.repo, "commit", "-m", "external change")
    assert Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run() == RunStatus.paused
    assert "HEAD moved" in workspace.load_state().run.status_reason


def test_branch_switch_during_clarify_fails_verification(workspace, scenario, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"clarify": "switch_branch"}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.retry.max_attempts_execution = 1

    assert Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run() == RunStatus.halted

    state = workspace.load_state()
    assert state.features[0].status == FeatureStatus.failed
    assert "branch changed" in state.features[0].block_reason


def test_default_permissions_are_logged_and_no_push_is_attempted(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    push_calls = []
    monkeypatch.setattr(gitops, "push", lambda *args: push_calls.append(args))

    assert Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run() == RunStatus.finished

    records = calls(path)
    claude = next(record for record in records if record["agent"] == "claude")
    opencode = next(record for record in records if record["agent"] == "opencode")
    assert "--permission-mode" in claude["argv"]
    assert claude["argv"][claude["argv"].index("--permission-mode") + 1] == "dontAsk"
    assert "--allowedTools" in claude["argv"]
    assert "Write(specs/**)" in claude["argv"]
    assert "--disallowedTools" in claude["argv"]
    assert "Bash(git push*)" in claude["argv"]
    assert "--dangerously-skip-permissions" not in claude["argv"]
    assert "bypassPermissions" not in claude["argv"]
    assert "--standalone" in opencode["argv"]
    assert "--auto" in opencode["argv"]
    permissions = json.loads(opencode["opencode_config_content"])["permission"]
    assert permissions["bash"]["git push*"] == "deny"
    assert _git(workspace.repo, "remote").stdout.strip() == ""
    assert push_calls == []


def test_configured_push_publishes_feature_branch(workspace, scenario, monkeypatch, tmp_path):
    _add_feature(workspace)
    path = scenario({"claude": {}, "opencode": {}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    _git(workspace.repo, "remote", "add", "origin", str(remote))
    config = load_config(workspace.config_path)
    config.git.push = True

    assert Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run() == RunStatus.finished

    assert _git(remote, "show-ref", "--verify", "refs/heads/orchestrator/F1").returncode == 0
    assert workspace.load_state().features[0].status == FeatureStatus.completed
