from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .models import ProcessResult


def _terminate(proc: subprocess.Popen, grace_s: int) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass


def run_process(
    argv: list[str],
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int | None = None,
    should_stop: Callable[[], bool] | None = None,
    grace_s: int = 10,
) -> ProcessResult:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agentpilot_") as tempdir:
        stdout_path, stderr_path = Path(tempdir) / "stdout.txt", Path(tempdir) / "stderr.txt"
        proc = _launch(argv, cwd, env, stdout_path, stderr_path)
        if isinstance(proc, ProcessResult):
            return proc.model_copy(update={"duration_s": time.monotonic() - start})
        timed_out, stopped = _wait_process(proc, start, timeout_s, should_stop, grace_s)
        return ProcessResult(
            exit_code=proc.returncode,
            timed_out=timed_out,
            stopped=stopped,
            stdout=_read_output(stdout_path),
            stderr=_read_output(stderr_path),
            duration_s=time.monotonic() - start,
        )


def _launch(argv: list[str], cwd: Path | str | None, env: dict[str, str] | None, stdout: Path, stderr: Path):
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    try:
        with stdout.open("wb") as stdout_file, stderr.open("wb") as stderr_file:
            return subprocess.Popen(
                argv, cwd=str(cwd) if cwd else None, env=process_env, stdin=subprocess.DEVNULL,
                stdout=stdout_file, stderr=stderr_file, start_new_session=True,
            )
    except OSError as error:
        stderr.write_text(str(error))
        return ProcessResult(exit_code=None, stderr=str(error))


def _wait_process(
    proc: subprocess.Popen,
    start: float,
    timeout_s: int | None,
    should_stop: Callable[[], bool] | None,
    grace_s: int,
) -> tuple[bool, bool]:
    while True:
        stopped = bool(should_stop and should_stop())
        timed_out = timeout_s is not None and time.monotonic() - start >= timeout_s
        if stopped or timed_out:
            _terminate(proc, grace_s)
            return timed_out, stopped
        try:
            proc.wait(timeout=0.2)
            return False, False
        except subprocess.TimeoutExpired:
            continue


def _read_output(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
