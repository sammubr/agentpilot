
from ai_development_orchestrator import agents
from ai_development_orchestrator.config import DEFAULT_CONFIG_TOML, load_config
from ai_development_orchestrator.models import ProcessResult


def test_build_claude_argv(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(p)
    argv = agents.build_claude_argv(cfg, "hello", None)
    assert "-p" in argv
    assert "--output-format" in argv
    assert "--permission-mode" in argv


def test_build_opencode_argv(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(p)
    argv = agents.build_opencode_argv(cfg, "hello", None)
    assert "run" in argv
    assert "--standalone" in argv
    assert "--auto" in argv
    assert "--format" in argv


def test_parse_claude_success():
    proc = ProcessResult(exit_code=0, stdout='{"type": "result", "is_error": false, "result": "ok", "session_id": "s1"}', stderr="")
    res = agents.parse_claude(proc)
    assert res.ok


def test_parse_claude_error():
    proc = ProcessResult(exit_code=1, stdout='{"type": "result", "is_error": true, "result": "bad"}', stderr="")
    res = agents.parse_claude(proc)
    assert not res.ok


def test_parse_opencode_error_events():
    proc = ProcessResult(exit_code=1, stdout='{"type":"step_start","sessionID":"s1"}\n{"type":"error","error":"fail"}\n', stderr="")
    res = agents.parse_opencode(proc)
    assert not res.ok
    assert res.session_id == "s1"


def test_parse_claude_result_success():
    from ai_development_orchestrator.agents import parse_claude
    from ai_development_orchestrator.models import ProcessResult

    proc = ProcessResult(exit_code=0, stdout='{"type": "result", "is_error": false, "result": "ok"}', stderr="")
    res = parse_claude(proc)
    assert res.ok


def test_parse_claude_error_result():
    from ai_development_orchestrator.agents import parse_claude
    from ai_development_orchestrator.models import ProcessResult

    proc = ProcessResult(exit_code=1, stdout='{"type": "result", "is_error": true, "result": "bad"}', stderr="")
    res = parse_claude(proc)
    assert not res.ok


def test_parse_claude_subtype_error():
    from ai_development_orchestrator.agents import parse_claude
    from ai_development_orchestrator.models import ProcessResult

    proc = ProcessResult(exit_code=1, stdout='{"type": "result", "subtype": "error_during_execution", "is_error": false, "result": "fail"}', stderr="")
    res = parse_claude(proc)
    assert not res.ok


def test_parse_claude_message_from_result_on_failure():
    from ai_development_orchestrator.agents import parse_claude
    from ai_development_orchestrator.models import ProcessResult

    proc = ProcessResult(exit_code=1, stdout='{"type": "result", "is_error": true, "result": "usage limit exceeded"}', stderr="")
    res = parse_claude(proc)
    assert not res.ok


def test_parse_claude_invalid_output_when_not_result_dict():
    from ai_development_orchestrator.agents import parse_claude
    from ai_development_orchestrator.models import FailureKind, ProcessResult

    proc = ProcessResult(exit_code=0, stdout='{"foo": "bar"}', stderr="")
    res = parse_claude(proc)
    assert not res.ok
    assert res.failure == FailureKind.invalid_output


def test_parse_claude_exit0_empty_stdout():
    from ai_development_orchestrator.agents import parse_claude
    from ai_development_orchestrator.models import FailureKind, ProcessResult

    proc = ProcessResult(exit_code=0, stdout="", stderr="")
    res = parse_claude(proc)
    assert not res.ok
    assert res.failure == FailureKind.invalid_output


def test_parse_opencode_text_in_part():
    from ai_development_orchestrator.agents import parse_opencode
    from ai_development_orchestrator.models import ProcessResult

    proc = ProcessResult(exit_code=0, stdout='{"type": "text", "part": {"text": "hello"}, "sessionID": "s1"}\n', stderr="")
    res = parse_opencode(proc)
    assert res.ok
    assert res.session_id == "s1"


def test_parse_opencode_error_from_error_data_message():
    from ai_development_orchestrator.agents import parse_opencode
    from ai_development_orchestrator.models import ProcessResult

    proc = ProcessResult(exit_code=1, stdout='{"type": "error", "error": {"data": {"message": "fail msg"}}}\n', stderr="")
    res = parse_opencode(proc)
    assert not res.ok


def test_parse_opencode_exit0_no_events():
    from ai_development_orchestrator.agents import parse_opencode
    from ai_development_orchestrator.models import FailureKind, ProcessResult

    proc = ProcessResult(exit_code=0, stdout="no json", stderr="")
    res = parse_opencode(proc)
    assert not res.ok
    assert res.failure == FailureKind.invalid_output


def test_agent_env_sets_spec_dir():
    from ai_development_orchestrator.agents import agent_env
    from ai_development_orchestrator.config import build_config
    from ai_development_orchestrator.models import AgentName

    cfg = build_config({})
    claude = agent_env(AgentName.claude, cfg, "specs/001-x")
    opencode = agent_env(AgentName.opencode, cfg, "specs/001-x")
    assert claude == {"SPECIFY_FEATURE_DIRECTORY": "specs/001-x"}
    assert opencode["SPECIFY_FEATURE_DIRECTORY"] == "specs/001-x"
    assert "OPENCODE_CONFIG_CONTENT" in opencode
    assert agent_env(AgentName.claude, cfg, None) == {}
