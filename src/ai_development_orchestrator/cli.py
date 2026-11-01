from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Annotated

import typer
import yaml

from .backlog import add_features, find_cycle, load_backlog_file
from .config import load_config
from .engine import Engine, StartRefused
from .gitops import repos
from .models import FeatureStatus, HistoryEvent, RunStatus, utcnow
from .report import format_status, status_data, write_report
from .workspace import LockHeld, Workspace

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
    skills = ["specify", "clarify", "plan", "tasks", "analyze", "converge"]
    missing = [name for name in skills if not (ws.repo / ".claude" / "skills" / f"speckit-{name}").is_dir()]
    checks.append(("Spec Kit skills", not missing, "present" if not missing else "missing " + ", ".join(missing)))
    cfg = None
    try:
        cfg = load_config(ws.config_path)
        checks.append(("Config", True, "loaded"))
    except (OSError, ValueError) as error:
        checks.append(("Config", False, str(error)))
    if cfg is not None:
        for name in ("claude", "opencode"):
            command = cfg.agents[name].command
            ok, detail = _version_check(command)
            checks.append((f"{name} --version", ok, detail))
    try:
        state = ws.load_state()
        ids = [feature.id for feature in state.features]
        known = set(ids)
        unknown = sorted({dep.id for feature in state.features for dep in feature.dependencies if dep.id not in known})
        cycle = find_cycle(state)
        valid = len(ids) == len(known) and not unknown and not cycle
        detail = "valid" if valid else f"duplicate ids={len(ids) - len(known)}, unknown dependencies={unknown}, cycle={cycle}"
        checks.append(("Backlog", valid, detail))
    except (OSError, ValueError) as error:
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


def _run(ws: Workspace, max_features: int | None, resume: bool) -> None:
    checks = _checks(ws)
    failures = [f"{name}: {detail}" for name, ok, detail in checks if not ok]
    if failures:
        _error("environment check failed: " + "; ".join(failures))
    if resume:
        ws.clear_control()
        state = ws.load_state()
        if state.run.status in (RunStatus.paused, RunStatus.stopped):
            state.run.status_reason = None
            ws.save_state(state)
    try:
        status = Engine(ws, _config(ws)).run(max_features=max_features)
    except LockHeld:
        _error("another orchestrator is running", 3)
    except (StartRefused, OSError, ValueError) as error:
        _error(str(error))
    state = ws.load_state()
    typer.echo(f"Run {status.value}" + (f": {state.run.status_reason}" if state.run.status_reason else ""))
    if status == RunStatus.halted:
        raise typer.Exit(1)


@app.command()
def run(ctx: typer.Context, max_features: Annotated[int | None, typer.Option("--max-features", min=1)] = None) -> None:
    _run(_workspace(ctx), max_features, resume=False)


@app.command()
def resume(ctx: typer.Context, max_features: Annotated[int | None, typer.Option("--max-features", min=1)] = None) -> None:
    _run(_workspace(ctx), max_features, resume=True)


@app.command()
def pause(ctx: typer.Context) -> None:
    ws = _workspace(ctx)
    ws.request("pause")
    typer.echo("Pause requested")


@app.command()
def stop(ctx: typer.Context) -> None:
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


@app.command()
def status(ctx: typer.Context, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    ws = _workspace(ctx)
    try:
        data = status_data(ws, ws.load_state(), utcnow())
    except (OSError, ValueError) as error:
        _error(str(error))
    typer.echo(json.dumps(data, indent=2) if json_output else format_status(data))


@app.command()
def retry(
    ctx: typer.Context,
    id: Annotated[str, typer.Argument()],
    from_step: Annotated[str | None, typer.Option("--from")] = None,
) -> None:
    ws = _workspace(ctx)
    _locked(ws)
    state = ws.load_state()
    feature = next((item for item in state.features if item.id == id), None)
    if feature is None:
        _error(f"unknown feature: {id}")
    if feature.status not in (FeatureStatus.blocked, FeatureStatus.failed):
        _error(f"feature {id} is not blocked or failed")
    if from_step is not None:
        failed_active_step = state.run.current_feature == id and state.run.active_step == from_step
        if from_step not in feature.completed_steps and not failed_active_step:
            _error(f"step not completed: {from_step}")
        if from_step in feature.completed_steps:
            index = feature.completed_steps.index(from_step)
            feature.completed_steps = feature.completed_steps[:index]
    else:
        feature.completed_steps = [step for step in feature.completed_steps if not step.startswith(("converge.", "correct."))]
        feature.correction_round = 0
    feature.attempts = {}
    feature.block_reason = None
    feature.finished_at = None
    feature.status = FeatureStatus.pending
    if state.run.pending_recovery and state.run.pending_recovery.feature_id == id:
        state.run.pending_recovery = None
    feature.history.append(HistoryEvent(at=utcnow(), step=from_step, event="retry_requested", detail="from " + from_step if from_step else "default"))
    ws.save_state(state)
    ws.log_event("retry_requested", feature=id, step=from_step)
    typer.echo(f"Retry queued for {id}")


@app.command()
def report(ctx: typer.Context, output: Annotated[Path | None, typer.Option("--output")] = None) -> None:
    ws = _workspace(ctx)
    state = ws.load_state()
    destination = output if output is None or output.is_absolute() else ws.repo / output
    try:
        written = write_report(ws, state, destination)
    except OSError as error:
        _error(str(error))
    typer.echo(str(written))
