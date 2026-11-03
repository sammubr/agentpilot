import os

from ai_development_orchestrator.workspace import LockHeld, Workspace


def test_atomic_write(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure_dirs()
    p = ws.orch / "test.txt"
    ws.atomic_write_text(p, "hello")
    assert p.read_text() == "hello"


def test_state_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.init()
    s = ws.load_state()
    ws.save_state(s)
    s2 = ws.load_state()
    assert s2.version == 2


def test_lock_exclusivity(tmp_path):
    ws = Workspace(tmp_path)
    ws.init()
    fd = ws.acquire_lock()
    ws2 = Workspace(tmp_path)
    try:
        ws2.acquire_lock()
        assert False
    except LockHeld:
        pass
    finally:
        os.close(fd)


def test_control_file(tmp_path):
    ws = Workspace(tmp_path)
    ws.init()
    ws.request("pause")
    assert ws.read_control() == "pause"
    ws.clear_control()
    assert ws.read_control() is None


def test_is_locked_false_when_pid_file_but_no_flock(tmp_path):
    import os

    from ai_development_orchestrator.workspace import Workspace

    ws = Workspace(tmp_path)
    ws.init()
    # write pid of current process but don't hold flock
    ws.lock_path.write_text(str(os.getpid()))
    assert ws.is_locked() is False


def test_lock_holder_pid_when_held(tmp_path):
    import os
    import subprocess
    import sys

    from ai_development_orchestrator.workspace import Workspace

    ws = Workspace(tmp_path)
    ws.init()
    lock_path = ws.lock_path
    code = f"""
import fcntl, os, time
fd = os.open({str(lock_path)!r}, os.O_RDWR|os.O_CREAT, 0o644)
os.ftruncate(fd, 0)
os.write(fd, str(os.getpid()).encode())
os.fsync(fd)
fcntl.flock(fd, fcntl.LOCK_EX)
open({str(tmp_path / 'LOCKED')!r},'w').write('ok')
time.sleep(10)
"""
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(tmp_path), env={**os.environ})
    import time as _t

    for _ in range(50):
        if (tmp_path / "LOCKED").exists():
            break
        _t.sleep(0.01)
    ws2 = Workspace(tmp_path)
    assert ws2.is_locked() is True
    pid = ws2.lock_holder_pid()
    assert pid == p.pid
    p.kill()
    p.wait()


def test_v1_state_is_migrated(tmp_path):
    import json

    ws = Workspace(tmp_path)
    ws.init()
    old = {
        "version": 1,
        "features": [
            {"id": "F1", "title": "t", "description": "d", "status": "in_progress", "spec_dir": "specs/001-x",
             "branch": "orchestrator/F1", "dependencies": [{"id": "F0", "added_by": "operator"}],
             "completed_steps": ["select", "specify", "clarify", "plan", "tasks", "analyze", "implement.handoff", "implement.run"]},
            {"id": "F0", "title": "t", "description": "d", "status": "failed"},
        ],
        "run": {"status": "halted"},
    }
    ws.state_path.write_text(json.dumps(old))
    state = ws.load_state()
    f1, f0 = state.features
    assert state.version == 2
    assert f1.status == "pending" and f1.branch == "orchestrator/F1"
    assert not hasattr(f1, "depends_on")
    assert f1.done == ["specify", "clarify", "plan", "tasks", "analyze", "implement"]
    assert f0.status == "blocked"
    assert state.run.status == "idle"


def test_v2_state_with_legacy_dependency_data_loads(tmp_path):
    import json

    ws = Workspace(tmp_path)
    ws.init()
    ws.state_path.write_text(json.dumps({
        "version": 2,
        "features": [{
            "id": "F1",
            "title": "first",
            "description": "legacy state",
            "depends_on": ["F0"],
        }],
    }))

    feature = ws.load_state().features[0]

    assert feature.id == "F1"
    assert not hasattr(feature, "depends_on")
    assert getattr(feature, "branch_base", None) is None
