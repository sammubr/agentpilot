from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .failures import classify, parse_reset_time
from .models import AgentName, AgentResult, FailureKind, ProcessResult, utcnow
from .process import run_process


def build_claude_argv(cfg: Config, prompt: str, session_id: str | None = None) -> list[str]:
    c = cfg.agents["claude"]
    argv = list(c.command)
    if session_id and cfg.retry.resume_sessions:
        argv += ["--resume", session_id]
    argv += ["-p", prompt]
    argv += ["--output-format", "json"]
    argv += ["--permission-mode", c.permission_mode]
    for t in c.allowed_tools:
        argv += ["--allowedTools", t]
    for t in c.disallowed_tools:
        argv += ["--disallowedTools", t]
    if c.model:
        argv += ["--model", c.model]
    argv += list(c.extra_args)
    return argv


def _failure(message: str, stderr: str, proc: ProcessResult, api_status=None, session_id=None) -> AgentResult:
    combined_fail = message + "\n" + stderr
    kind = classify(combined_fail, proc.timed_out, proc.exit_code, api_status)
    if kind is None and proc.timed_out:
        kind = FailureKind.timeout
    if kind is None:
        kind = FailureKind.execution_error
    reset_at = parse_reset_time(combined_fail, utcnow()) if kind == FailureKind.usage_limit else None
    return AgentResult(ok=False, failure=kind, message=str(message)[:2000], session_id=session_id, reset_at=reset_at, process=proc)


def parse_claude(proc: ProcessResult) -> AgentResult:
    text = proc.stdout
    stderr = proc.stderr
    combined = (text + "\n" + stderr).strip()
    try:
        if not text.strip():
            if proc.exit_code == 0:
                return AgentResult(ok=False, failure=FailureKind.invalid_output, message=combined[:2000], process=proc)
            return _failure(combined, "", proc)
        data = json.loads(text.strip())
    except (OSError, ValueError):
        if proc.exit_code == 0:
            return AgentResult(ok=False, failure=FailureKind.invalid_output, message=combined[:2000], process=proc)
        return _failure(combined, "", proc)
    if isinstance(data, dict):
        t = data.get("type")
        is_error = data.get("is_error")
        subtype = data.get("subtype") or ""
        sid = data.get("session_id") or data.get("sessionId") or data.get("sessionID")
        if t == "result" and (is_error is False or is_error is None) and not (isinstance(subtype, str) and subtype.startswith("error")):
            msg = data.get("result")
            if msg is None:
                msg = json.dumps(data)
            return AgentResult(ok=True, message=str(msg)[:4000], session_id=sid, process=proc)
        if t == "result":
            msg = data.get("result") if data.get("result") is not None else (data.get("error") or data.get("message") or subtype or json.dumps(data))
            api_status = data.get("api_error_status") or data.get("status")
            return _failure(str(msg), stderr, proc, api_status, sid)
        if proc.exit_code == 0:
            return AgentResult(ok=False, failure=FailureKind.invalid_output, message=combined[:2000], process=proc)
        api_status = data.get("api_error_status") or data.get("status")
        msg = data.get("result") if data.get("result") is not None else (data.get("error") or data.get("message") or json.dumps(data))
        return _failure(str(msg), stderr, proc, api_status, sid)
    if proc.exit_code == 0:
        return AgentResult(ok=False, failure=FailureKind.invalid_output, message=combined[:2000], process=proc)
    return _failure(combined, "", proc)


def build_opencode_argv(cfg: Config, prompt: str, session_id: str | None = None) -> list[str]:
    c = cfg.agents["opencode"]
    argv = list(c.command)
    argv += ["run", "--standalone", "--auto", "--format", "json"]
    if session_id and cfg.retry.resume_sessions:
        argv += ["--session", session_id]
    if c.model:
        argv += ["--model", c.model]
    argv += list(c.extra_args)
    argv += [prompt]
    return argv


def opencode_env(cfg: Config) -> dict[str, str]:
    return {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": cfg.agents["opencode"].permission})}


def parse_opencode(proc: ProcessResult) -> AgentResult:
    events = [event for line in proc.stdout.splitlines() if (event := _json_event(line)) is not None]
    session_id = next((event.get("sessionID") or event.get("session_id") for event in events if event.get("sessionID") or event.get("session_id")), None)
    errors = [message for event in events if (message := _event_error(event))]
    combined = proc.stdout + "\n" + proc.stderr
    if errors:
        return _opencode_failure(" ".join(errors), combined, proc, session_id)
    if proc.exit_code != 0:
        return _opencode_failure(combined, combined, proc, session_id)
    if not events:
        return AgentResult(ok=False, failure=FailureKind.invalid_output, message=combined.strip()[:2000], process=proc)
    messages = [text for event in events for text in _event_text(event)]
    message = " ".join(messages) if messages else proc.stdout
    return AgentResult(ok=True, message=message[:4000], session_id=session_id, process=proc)


def _json_event(line: str) -> dict | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _event_error(event: dict) -> str | None:
    error = event.get("error")
    if event.get("type") != "error" and error is None:
        return None
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])
        return str(error.get("message") or error)
    return str(error or event)


def _event_text(event: dict) -> list[str]:
    texts = []
    part = event.get("part")
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        texts.append(part["text"])
    if isinstance(event.get("text"), str):
        texts.append(event["text"])
    for item in event.get("parts", []):
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return texts


def _opencode_failure(message: str, combined: str, proc: ProcessResult, session_id: str | None) -> AgentResult:
    kind = classify(message + "\n" + combined, proc.timed_out, proc.exit_code) or FailureKind.execution_error
    reset_at = parse_reset_time(message + "\n" + combined, utcnow()) if kind == FailureKind.usage_limit else None
    return AgentResult(ok=False, failure=kind, message=message[:2000], session_id=session_id, reset_at=reset_at, process=proc)


def agent_env(agent: AgentName, cfg: Config, spec_dir: str | None) -> dict[str, str]:
    env = opencode_env(cfg) if agent == AgentName.opencode else {}
    if spec_dir:
        env["SPECIFY_FEATURE_DIRECTORY"] = spec_dir
    return env


def run_agent(
    agent: AgentName,
    cfg: Config,
    message: str,
    repo: Path,
    spec_dir: str | None = None,
    session_id: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AgentResult:
    if agent == AgentName.claude:
        argv, parse = build_claude_argv(cfg, message, session_id), parse_claude
    else:
        argv, parse = build_opencode_argv(cfg, message, session_id), parse_opencode
    env = agent_env(agent, cfg, spec_dir)
    timeout = cfg.agents[agent.value].timeout_s
    return parse(run_process(argv, cwd=repo, env=env, timeout_s=timeout, should_stop=should_stop))
