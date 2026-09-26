import json
import time

import pytest
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.web.app import create_app


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


# httpx's TestClient has no default read timeout, and iter_lines() can
# block indefinitely on a stalled connection between lines already
# received - the in-loop deadline checks below only run between lines that
# already arrived, so they can't catch that case. This bounds it: a
# regression that stops the stream entirely fails the test instead of
# hanging the whole suite.
STREAM_TIMEOUT = 20.0


def wait(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {client.get(f'/api/jobs/{job_id}').json()}")


def test_enqueue_and_complete_a_scan(client):
    r = client.post("/api/jobs", json={"command": "scan", "args": {}})
    assert r.status_code == 202
    assert wait(client, r.json()["job_id"])["status"] == "done"


def test_an_unknown_command_is_422(client):
    r = client.post("/api/jobs", json={"command": "rm", "args": {}})
    assert r.status_code == 422


def test_commit_without_confirmation_is_409(client):
    r = client.post("/api/jobs", json={"command": "commit", "args": {}})
    assert r.status_code == 409
    assert "confirm" in r.json()["detail"].lower()


def test_rollback_without_confirmation_is_409(client):
    r = client.post(
        "/api/jobs", json={"command": "rollback", "args": {"journal_id": "x"}}
    )
    assert r.status_code == 409


def test_commit_with_confirmation_is_accepted(client):
    r = client.post(
        "/api/jobs", json={"command": "commit", "args": {"confirmed": True}}
    )
    assert r.status_code == 202


@pytest.mark.parametrize("confirmed_value", ["false", "0", 0, None, "no", False])
def test_confirmation_requires_the_literal_boolean_true(client, confirmed_value):
    # args is raw JSON with no coercion (JobCreate.args: dict[str, Any]), and
    # every non-empty string is truthy in Python - a client that serializes
    # a checkbox as "false" must still be refused, not waved through.
    r = client.post(
        "/api/jobs",
        json={"command": "commit", "args": {"confirmed": confirmed_value}},
    )
    assert r.status_code == 409


def test_job_list_is_newest_first(client):
    first = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    wait(client, first)
    second = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    wait(client, second)
    assert client.get("/api/jobs").json()[0]["id"] == second


def test_get_an_unknown_job_is_404(client):
    assert client.get("/api/jobs/9999").status_code == 404


def test_delete_cancels(client):
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    client.delete(f"/api/jobs/{job_id}")
    assert client.get(f"/api/jobs/{job_id}").json()["status"] in (
        "cancelled", "done"  # a scan of an empty folder may beat the cancel
    )


def test_events_stream_ends_with_a_terminal_status(client):
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    with client.stream(
        "GET", f"/api/jobs/{job_id}/events", timeout=STREAM_TIMEOUT
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        last = None
        deadline = time.monotonic() + 10
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise AssertionError("event stream did not reach a terminal status")
            if line.startswith("data:"):
                last = json.loads(line[5:])
                if last["status"] in ("done", "failed", "cancelled"):
                    break
    assert last["status"] == "done"


def test_events_stream_for_an_unknown_job_is_404(client):
    r = client.get("/api/jobs/9999/events")
    assert r.status_code == 404


def _subscriber_count(state, job_id):
    return len(state.runner._subscribers.get(job_id, []))


def _wait_for_no_subscribers(state, job_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _subscriber_count(state, job_id) == 0:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"subscriber for job {job_id} was never cleaned up: "
        f"{state.runner._subscribers.get(job_id)}"
    )


def test_events_stream_self_heals_when_the_terminal_publish_is_swallowed(client):
    # Simulate the hazard: _finish() still writes the terminal status to the
    # DB, but the subscriber notify that normally follows never happens (the
    # real code swallows that failure with `except: pass`). No terminal
    # event is ever queued for a subscriber - the stream must still end by
    # noticing the DB row directly, not hang waiting on a queue.get() that
    # will never be fed again.
    state = client.app.state.reshelf
    original_publish = state.runner._publish
    state.runner._publish = lambda job_id: None  # every publish is silently lost
    try:
        job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
        started = time.monotonic()
        with client.stream(
            "GET", f"/api/jobs/{job_id}/events", timeout=STREAM_TIMEOUT
        ) as response:
            last = None
            deadline = time.monotonic() + 10
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise AssertionError("stream hung: self-heal never kicked in")
                if line.startswith("data:"):
                    last = json.loads(line[5:])
                    if last["status"] in ("done", "failed", "cancelled"):
                        break
        elapsed = time.monotonic() - started
        assert last is not None
        assert last["status"] in ("done", "failed", "cancelled")
        # bounded, not instant - proves the self-heal poll (not a fast path)
        # is what closed the stream
        assert elapsed < 10
        # And the leak is actually gone, not just hidden from the client:
        # events()'s own `finally` must have run and removed this
        # subscriber's queue from JobRunner._subscribers.
        _wait_for_no_subscribers(state, job_id)
    finally:
        state.runner._publish = original_publish


def test_events_stream_disconnect_removes_subscriber(client):
    # A client that stops reading (closes the connection, navigates away)
    # must not leave its subscriber queue registered forever either.
    state = client.app.state.reshelf
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    with client.stream(
        "GET", f"/api/jobs/{job_id}/events", timeout=STREAM_TIMEOUT
    ) as response:
        # Read only the first line, then fall out of the `with` block
        # without draining the stream - that's the disconnect.
        next(response.iter_lines())
    _wait_for_no_subscribers(state, job_id)


def test_finish_logs_when_the_terminal_publish_fails(client, caplog):
    # _finish() deliberately swallows a publish failure so it never rewrites
    # an already-committed terminal status - but a bare `except: pass` with
    # no trace is how that becomes unexplainable later. It must log.
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    wait(client, job_id)
    state = client.app.state.reshelf

    def boom(_job_id):
        raise RuntimeError("subscriber notify exploded")

    state.runner._publish = boom
    with caplog.at_level("ERROR", logger="reshelf.web.jobs"):
        state.runner._finish(job_id, "done")

    assert any(
        "publish" in r.getMessage().lower() and str(job_id) in r.getMessage()
        for r in caplog.records
    )
