from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_TOML
from .models import State, utcnow


class LockHeld(Exception):
    pass


class Workspace:
    def __init__(self, repo: Path | str):
        self.repo = Path(repo)
        self.orch = self.repo / ".orchestrator"
        self.config_path = self.orch / "config.toml"
        self.state_path = self.orch / "state.json"
        self.lock_path = self.orch / "lock"
        self.control_path = self.orch / "control"
        self.report_path = self.orch / "report.md"
        self.logs_dir = self.orch / "logs"
        self.events_path = self.logs_dir / "events.jsonl"
        self.runs_dir = self.orch / "runs"
        self._lock_fd: int | None = None

    def ensure_dirs(self) -> None:
        self.orch.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def atomic_write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp." + str(os.getpid()))
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except (OSError, ValueError):
            try:
                if tmp.exists():
                    tmp.unlink()
            except (OSError, ValueError):
                pass
            raise

    def atomic_write_json(self, path: Path, obj: Any) -> None:
        text = json.dumps(obj, ensure_ascii=False)
        self.atomic_write_text(path, text)

    def load_state(self) -> State:
        if not self.state_path.exists():
            return State()
        data = json.loads(self.state_path.read_text())
        return State.model_validate(data)

    def save_state(self, state: State) -> None:
        data = state.model_dump(mode="json")
        self.atomic_write_json(self.state_path, data)

    def init(self, force: bool = False) -> None:
        self.ensure_dirs()
        gitignore = self.orch / ".gitignore"
        if not gitignore.exists() or force:
            gitignore.write_text("*\n!.gitignore\n!config.toml\n")
        if not self.config_path.exists() or force:
            self.config_path.write_text(DEFAULT_CONFIG_TOML)
        if not self.state_path.exists() or force:
            self.save_state(State())
        if not self.events_path.exists():
            self.events_path.touch()

    def acquire_lock(self) -> int:
        self.ensure_dirs()
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise LockHeld(str(e))
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        self._lock_fd = fd
        return fd

    def lock_holder_pid(self) -> int | None:
        if not self.lock_path.exists():
            return None
        if not self.is_locked():
            return None
        try:
            data = self.lock_path.read_text().strip()
            if not data:
                return None
            return int(data)
        except (OSError, ValueError):
            return None

    def is_locked(self) -> bool:
        if not self.orch.exists():
            return False
        if not self.lock_path.exists():
            return False
        try:
            fd = os.open(self.lock_path, os.O_RDWR, 0o644)
        except (OSError, ValueError):
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
        finally:
            try:
                os.close(fd)
            except (OSError, ValueError):
                pass

    def release_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except (OSError, ValueError):
                pass
            try:
                os.close(self._lock_fd)
            except (OSError, ValueError):
                pass
            self._lock_fd = None

    def request(self, control: str) -> None:
        self.atomic_write_text(self.control_path, control)

    def read_control(self) -> str | None:
        if not self.control_path.exists():
            return None
        try:
            return self.control_path.read_text().strip() or None
        except (OSError, ValueError):
            return None

    def clear_control(self) -> None:
        if self.control_path.exists():
            try:
                self.control_path.unlink()
            except (OSError, ValueError):
                pass

    def log_event(self, event: str, feature: str | None = None, step: str | None = None, **fields: Any) -> None:
        rec: dict[str, Any] = {
            "at": utcnow().isoformat(),
            "event": event,
        }
        if feature is not None:
            rec["feature"] = feature
        if step is not None:
            rec["step"] = step
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def new_run_dir(self, feature: str, step: str, attempt: int) -> Path:

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        d = self.runs_dir / f"{ts}-{feature}-{step}-a{attempt}"
        d.mkdir(parents=True, exist_ok=True)
        return d
