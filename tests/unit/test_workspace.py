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
    assert s2.version == 1


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
