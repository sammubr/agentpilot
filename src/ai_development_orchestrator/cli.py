from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Annotated

import typer
import yaml

from .backlog import add_features, load_backlog_file
from .config import load_config
from .engine import Engine, StartRefused
from .gitops import repos
from .models import FeatureStatus, RunStatus, State
from .workspace import LockHeld, Workspace

SKILLS = ("specify", "clarify", "plan", "tasks", "analyze", "converge")

app = typer.Typer(no_args_is_help=True, help="Autonomous AI development orchestrator")


@app.callback()
def main(ctx: typer.Context, repo: Annotated[Path | None, typer.Option("--repo")] = None) -> None:
    ctx.obj = Workspace((repo or Path.cwd()).expanduser().resolve())


def _workspace(ctx: typer.Context) -> Workspace:
    return ctx.obj


def _error(message: str, code: int = 1) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code)


def _config(ws: Workspace):
    try:
        return load_config(ws.config_path)
    except (OSError, ValueError) as error:
        _error(f"config error: {error}")


def _locked(ws: Workspace) -> None:
    if ws.is_locked():
        _error("another orchestrator is running", 3)


@app.command()
def init(ctx: typer.Context, force: Annotated[bool, typer.Option("--force")] = False) -> None:
    ws = _workspace(ctx)
    if ws.config_path.exists() and not force:
        _error(f"already initialized: {ws.config_path}")
    ws.init(force=force)
    typer.echo(f"Initialized {ws.orch}")


@app.command()
def add(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument()],
    title: Annotated[str, typer.Argument()],
    description: Annotated[str, typer.Option("--description")],
    depends_on: Annotated[list[str] | None, typer.Option("--depends-on")] = None,
) -> None:
    ws = _workspace(ctx)
    _locked(ws)
    try:
        state = ws.load_state()
        add_features(state, [{"id": id, "title": title, "description": description, "depends_on": depends_on or []}])
        ws.save_state(state)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _error(str(error))
    typer.echo(f"Added {id}")


@app.command(name="import")
def import_backlog(ctx: typer.Context, file: Annotated[Path, typer.Argument()]) -> None:
    ws = _workspace(ctx)
    _locked(ws)
    try:
        state = ws.load_state()
        add_features(state, load_backlog_file(file))
        ws.save_state(state)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        _error(str(error))
    typer.echo(f"Imported backlog from {file}")


def _checks(ws: Workspace) -> list[tuple[str, bool, str]]:
    found = repos(ws.repo)
    checks = [("Git repository", bool(found), ", ".join(str(path) for path in found) or f"none in {ws.repo}")]
    checks.append((".specify/", (ws.repo / ".specify").is_dir(), "present" if (ws.repo / ".specify").is_dir() else "missing"))
    missing = [f"speckit-{name}" for name in SKILLS if not (ws.repo / ".claude" / "skills" / f"speckit-{name}").is_dir()]
    if not (ws.repo / ".opencode" / "commands" / "speckit.implement.md").is_file():
        missing.append(".opencode/commands/speckit.implement.md")
    checks.append(("Spec Kit commands", not missing, "present" if not missing else "missing " + ", ".join(missing)))
    try:
        cfg = load_config(ws.config_path)
        checks.append(("Config", True, "loaded"))
        checks += [(f"{name} --version", *_version_check(cfg.agents[name].command)) for name in ("claude", "opencode")]
    except (OSError, ValueError) as error:
        checks.append(("Config", False, str(error)))
    try:
        state = ws.load_state()
        add_features(State(), [{**f.model_dump(), "depends_on": f.depends_on} for f in state.features])
        checks.append(("Backlog", True, "valid"))
    except (OSError, ValueError, KeyError) as error:
        checks.append(("Backlog", False, str(error)))
    return checks


def _version_check(command: list[str]) -> tuple[bool, str]:
    if not command:
        return False, "empty command"
    try:
        result = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"exit {result.returncode}"
    return result.returncode == 0, detail


@app.command()
def check(ctx: typer.Context) -> None:
    results = _checks(_workspace(ctx))
    for name, ok, detail in results:
        typer.echo(f'{"OK  " if ok else "FAIL"} {name}: {detail}')
    if any(not ok for _, ok, _ in results):
        raise typer.Exit(1)


def _run(ws: Workspace, max_features: int | None, run_id: str | None = None) -> None:
    failures = [f"{name}: {detail}" for name, ok, detail in _checks(ws) if not ok]
    if failures:
        _error("environment check failed: " + "; ".join(failures))
    if ws.is_locked():
        _error("another orchestrator is running", 3)
    ws.clear_control()
    try:
        status = Engine(ws, _config(ws)).run(max_features=max_features, run_id=run_id)
    except LockHeld:
        _error("another orchestrator is running", 3)
    except (StartRefused, OSError, ValueError) as error:
        _error(str(error))
    reason = ws.load_state().run.reason
    typer.echo(f"Run {status.value}" + (f": {reason}" if reason else ""))


MaxFeatures = Annotated[int | None, typer.Option("--max-features", min=1)]


@app.command()
def run(ctx: typer.Context, max_features: MaxFeatures = None) -> None:
    """Start a run session."""
    _run(_workspace(ctx), max_features)


@app.command(name="continue")
def continue_run(ctx: typer.Context, max_features: MaxFeatures = None) -> None:
    """Start a new run session from the preserved progress."""
    _run(_workspace(ctx), max_features)


@app.command()
def resume(ctx: typer.Context) -> None:
    """Resume a paused run."""
    ws = _workspace(ctx)
    if ws.is_locked():
        ws.request("resume")
        typer.echo("Resume requested")
        return
    state = ws.load_state()
    if state.run.status == RunStatus.stopped:
        _error("run was stopped; use continue")
    if state.run.status != RunStatus.paused:
        _error(f"no paused run (status: {state.run.status.value}); use continue")
    _run(ws, None, state.run.id)


@app.command()
def pause(ctx: typer.Context) -> None:
    """Pause the active run after the current agent call."""
    _workspace(ctx).request("pause")
    typer.echo("Pause requested")


@app.command()
def stop(ctx: typer.Context) -> None:
    """Stop the run, terminating the current agent call; progress is kept."""
    ws = _workspace(ctx)
    ws.request("stop")
    pid = ws.lock_holder_pid()
    if pid is None:
        typer.echo("Stop requested; no run currently holds the lock")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        typer.echo(f"Stop requested; sent SIGTERM to process {pid}")
    except ProcessLookupError:
        typer.echo("Stop requested; lock holder has already exited")
    except PermissionError as error:
        _error(f"could not signal process {pid}: {error}")


def _status_data(ws: Workspace, state: State) -> dict:
    run = state.run.model_dump(mode="json")
    features = [
        f.model_dump(mode="json", include={"id", "title", "status", "spec_dir", "done", "round", "attempts", "last_error", "depends_on"})
        for f in state.features
    ]
    unavailable = {agent.value: until.isoformat() for agent, until in state.unavailable.items()}
    return {"run": run, "running": ws.is_locked(), "unavailable": unavailable, "features": features}


def _format_status(data: dict) -> str:
    run = data["run"]
    lines = [
        f'Run {run["id"] or "-"}: {run["status"]}' + (f' ({run["reason"]})' if run["reason"] else "")
        + f' — process: {"running" if data["running"] else "none"}',
        f'Current: {run["feature"] or "-"}   Stage: {run["stage"] or "-"}   Agent: {run["agent"] or "-"}',
    ]
    lines += [f"Unavailable: {agent} until {until}" for agent, until in data["unavailable"].items()]
    for f in data["features"]:
        stage = f["done"][-1] if f["done"] else "-"
        error = f'   error: {f["last_error"]}' if f["last_error"] else ""
        deps = f' (depends on {", ".join(f["depends_on"])})' if f["depends_on"] else ""
        lines.append(f'  {f["id"]}{deps}: {f["status"]}   last stage: {stage}   round: {f["round"]}   attempts: {f["attempts"]}{error}')
    return "\n".join(lines)


@app.command()
def status(ctx: typer.Context, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    ws = _workspace(ctx)
    try:
        data = _status_data(ws, ws.load_state())
    except (OSError, ValueError) as error:
        _error(str(error))
    typer.echo(json.dumps(data, indent=2) if json_output else _format_status(data))


@app.command()
def retry(ctx: typer.Context, id: Annotated[str, typer.Argument()]) -> None:
    """Requeue a blocked feature; its stage is re-derived from the Spec Kit artifacts."""
    ws = _workspace(ctx)
    _locked(ws)
    state = ws.load_state()
    feature = next((item for item in state.features if item.id == id), None)
    if feature is None:
        _error(f"unknown feature: {id}")
    if feature.status != FeatureStatus.blocked:
        _error(f"feature {id} is not blocked")
    feature.status = FeatureStatus.pending
    feature.attempts, feature.round, feature.session_id, feature.last_error = 0, 0, None, None
    ws.save_state(state)
    ws.log_event("retry_requested", feature=id)
    typer.echo(f"Retry queued for {id}")
