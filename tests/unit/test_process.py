import time

from ai_development_orchestrator import process


def test_process_large_output(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # child printing 1MB
    argv = ["python3", "-c", "import sys; sys.stdout.write('a'*1024*1024); sys.stdout.flush()"]
    res = process.run_process(argv, run_dir=run_dir)
    assert res.exit_code == 0
    assert len(res.stdout) >= 1024 * 1024
    assert (run_dir / "stdout.txt").read_text() == res.stdout


def test_process_timeout_kills_process_group(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # spawn grandchild
    argv = ["bash", "-c", "sleep 100 & sleep 100"]
    res = process.run_process(argv, timeout_s=1, run_dir=run_dir, grace_s=1)
    assert res.timed_out
    assert res.exit_code is None or res.exit_code < 0


def test_process_should_stop(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stopped_flag = [False]

    def should_stop():
        return stopped_flag[0]

    def set_stop():
        time.sleep(0.5)
        stopped_flag[0] = True

    import threading

    t = threading.Thread(target=set_stop)
    t.start()
    argv = ["sleep", "100"]
    res = process.run_process(argv, run_dir=run_dir, should_stop=should_stop, grace_s=1)
    t.join()
    assert res.stopped


def test_process_missing_executable(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    res = process.run_process(["this_command_does_not_exist_12345"], run_dir=run_dir)
    assert res.exit_code is None
