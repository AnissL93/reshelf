import threading
import time

import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.pipeline import JobCancelled
from reshelf.store.sidecar import SidecarStore
from reshelf.web.jobs import COMMANDS, JobRunner, UnknownCommand


@pytest.fixture
def runner(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    r = JobRunner(cfg, db, SidecarStore(cfg))
    r.start()
    yield r
    r.stop()
    db.close()


def wait_for(runner, job_id, status, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job and job["status"] == status:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {status}: {runner.get(job_id)}")


def test_a_job_runs_and_reports_done(runner, monkeypatch):
    monkeypatch.setitem(
        COMMANDS, "noop", lambda cfg, db, store, args, progress: {"ok": args["n"]}
    )
    job_id = runner.enqueue("noop", {"n": 7})
    job = wait_for(runner, job_id, "done")
    assert '"ok": 7' in job["message"] or "7" in job["message"]


def test_an_unknown_command_is_refused_at_enqueue_time(runner):
    with pytest.raises(UnknownCommand):
        runner.enqueue("rm -rf /", {})


def test_a_failing_job_records_the_error_and_does_not_kill_the_worker(
    runner, monkeypatch
):
    def boom(cfg, db, store, args, progress):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(COMMANDS, "boom", boom)
    monkeypatch.setitem(COMMANDS, "fine", lambda *a: "ok")

    failed = runner.enqueue("boom", {})
    job = wait_for(runner, failed, "failed")
    assert "kaboom" in job["error"]

    after = runner.enqueue("fine", {})
    wait_for(runner, after, "done")


def test_progress_updates_are_recorded(runner, monkeypatch):
    def counting(cfg, db, store, args, progress):
        for n in range(1, 4):
            progress(n, 3, f"step {n}")
        return "done"

    monkeypatch.setitem(COMMANDS, "counting", counting)
    job_id = runner.enqueue("counting", {})
    job = wait_for(runner, job_id, "done")
    assert job["progress"] == 3
    assert job["total"] == 3
    assert "step 3" in job["log"]


def test_cancel_stops_a_running_job(runner, monkeypatch):
    def slow(cfg, db, store, args, progress):
        for n in range(200):
            progress(n, 200, "working")
            time.sleep(0.01)

    monkeypatch.setitem(COMMANDS, "slow", slow)
    job_id = runner.enqueue("slow", {})
    deadline = time.monotonic() + 3
    while runner.get(job_id)["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.cancel(job_id) is True
    wait_for(runner, job_id, "cancelled")


def test_only_one_job_runs_at_a_time(runner, monkeypatch):
    concurrent = []
    running = []

    def tracked(cfg, db, store, args, progress):
        running.append(1)
        concurrent.append(len(running))
        time.sleep(0.05)
        running.pop()

    monkeypatch.setitem(COMMANDS, "tracked", tracked)
    ids = [runner.enqueue("tracked", {}) for _ in range(3)]
    for job_id in ids:
        wait_for(runner, job_id, "done")
    assert max(concurrent) == 1


def test_stale_jobs_are_marked_interrupted_on_start(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO jobs (command, status, created_at) VALUES ('scan','running','x')"
    )
    db.conn.execute(
        "INSERT INTO jobs (command, status, created_at) VALUES ('match','queued','x')"
    )
    db.conn.commit()

    r = JobRunner(cfg, db, SidecarStore(cfg))
    r.start()
    try:
        statuses = {j["command"]: j["status"] for j in r.recent()}
        assert statuses == {"scan": "interrupted", "match": "interrupted"}
    finally:
        r.stop()
        db.close()


def test_events_streams_progress_then_a_terminal_event(runner, monkeypatch):
    def two_steps(cfg, db, store, args, progress):
        progress(1, 2, "half")
        progress(2, 2, "all")

    monkeypatch.setitem(COMMANDS, "two", two_steps)
    job_id = runner.enqueue("two", {})
    seen = []
    for event in runner.events(job_id):
        seen.append(event)
        if event["status"] in ("done", "failed", "cancelled"):
            break
    assert seen[-1]["status"] == "done"


def test_cancelling_a_queued_job_prevents_it_from_running(runner, monkeypatch):
    ran = []
    gate = threading.Event()

    def slow_first(cfg, db, store, args, progress):
        gate.wait(3)

    def marks_if_run(cfg, db, store, args, progress):
        ran.append(1)

    monkeypatch.setitem(COMMANDS, "slow_first", slow_first)
    monkeypatch.setitem(COMMANDS, "marks_if_run", marks_if_run)

    first = runner.enqueue("slow_first", {})
    deadline = time.monotonic() + 3
    while runner.get(first)["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.get(first)["status"] == "running"

    second = runner.enqueue("marks_if_run", {})
    assert runner.cancel(second) is True

    gate.set()
    wait_for(runner, first, "done")
    wait_for(runner, second, "cancelled")
    assert ran == []


def test_a_bookkeeping_failure_does_not_kill_the_worker(runner, monkeypatch):
    calls = {"n": 0}
    original_publish = JobRunner._publish

    def flaky_publish(self, job_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bookkeeping boom")
        return original_publish(self, job_id)

    monkeypatch.setattr(JobRunner, "_publish", flaky_publish)
    monkeypatch.setitem(COMMANDS, "fine", lambda *a: "ok")

    first = runner.enqueue("fine", {})
    wait_for(runner, first, "failed")

    second = runner.enqueue("fine", {})
    wait_for(runner, second, "done")


def test_the_real_pipeline_commands_are_registered():
    assert {
        "scan", "extract", "match", "resolve", "plan", "commit", "rollback",
        "reindex", "rematch", "convert",
    } <= set(COMMANDS)
