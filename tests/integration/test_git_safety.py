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


def _paused_reasons(workspace) -> list[str]:
    events = [json.loads(line) for line in workspace.events_path.read_text().splitlines()]
    return [event["reason"] for event in events if event["event"] == "paused"]


def test_stray_file_between_stages_pauses(workspace, scenario, monkeypatch, drive):
    _add_feature(workspace)
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({"claude": {"plan": "request_pause"}}))
    engine = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False)
    stray = workspace.repo / "stray.txt"

    def pause_hook():
        if not stray.exists():
            stray.write_text("unexpected\n")
            return "resume"
        stray.unlink()
        return "resume"

    assert drive(engine, controls=(pause_hook, pause_hook)) == RunStatus.finished
    reasons = _paused_reasons(workspace)
    assert reasons[0] == "pause requested"
    assert "unexpected_changes: stray.txt" in reasons[1]
    assert workspace.load_state().features[0].status == FeatureStatus.completed


@pytest.mark.parametrize("behavior, reason", [("commit", "HEAD moved during stage"), ("switch_branch", "branch changed")])
def test_agent_git_mutation_pauses(workspace, scenario, monkeypatch, drive, behavior, reason):
    _add_feature(workspace)
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({"claude": {"clarify": behavior}}))
    engine = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False)

    assert drive(engine, controls=("stop",)) == RunStatus.stopped

    assert reason in _paused_reasons(workspace)[0]
    state = workspace.load_state()
    assert "clarify" not in state.features[0].done


def test_continue_switches_back_to_feature_branch(workspace, scenario, calls, monkeypatch):
    _add_feature(workspace)
    path = scenario({"claude": {"plan": ["hang"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", path)
    config = load_config(workspace.config_path)
    config.agents["claude"].timeout_s = 1
    config.retry.max_attempts_execution = 1
    Engine(workspace, config, sleep=lambda _: None, handle_signals=False).run()
    state = workspace.load_state()
    state.features[0].status = FeatureStatus.pending
    workspace.save_state(state)
    fresh = load_config(workspace.config_path)
    _git(workspace.repo, "checkout", "-q", "-")
    assert gitops.current_branch(workspace.repo) != "orchestrator/F1"
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario({}))

    assert Engine(workspace, fresh, sleep=lambda _: None, handle_signals=False).run() == RunStatus.finished
    assert gitops.current_branch(workspace.repo) == "orchestrator/F1"
    assert workspace.load_state().features[0].status == FeatureStatus.completed


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
