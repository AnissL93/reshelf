import asyncio
import contextlib
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.planner.planner import generate_plan
from reshelf.store.models import Book, FileEntry
from reshelf.web.api.jobs import get_journal, get_plan, job_events
from reshelf.web.app import create_app
from reshelf.web.jobs import COMMANDS


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


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


def _register_slow_command(monkeypatch):
    # A command that is provably still running (it reports progress in a
    # loop for up to 2s) rather than one that may already be finished by
    # the time a test gets around to checking it - the same technique
    # tests/test_jobs.py::test_cancel_stops_a_running_job uses.
    def slow(cfg, db, store, args, progress):
        for n in range(200):
            progress(n, 200, "working")
            time.sleep(0.01)

    monkeypatch.setitem(COMMANDS, "slow", slow)


def test_delete_cancels(client, monkeypatch):
    # A plain "scan" of an empty folder can complete before the DELETE
    # lands, making "cancelled" vs. "done" a coin flip - not a tolerant
    # test, just a flaky one. Gate it instead on a job that is provably
    # still running when we cancel it, so the outcome is deterministic.
    _register_slow_command(monkeypatch)
    state = client.app.state.reshelf
    job_id = client.post("/api/jobs", json={"command": "slow", "args": {}}).json()[
        "job_id"
    ]

    deadline = time.monotonic() + 5
    while state.runner.get(job_id)["status"] != "running":
        if time.monotonic() > deadline:
            raise AssertionError("job never reached running")
        time.sleep(0.01)

    r = client.delete(f"/api/jobs/{job_id}")
    assert r.json()["cancelled"] is True
    assert wait(client, job_id)["status"] == "cancelled"


@pytest.mark.timeout(20)
def test_events_stream_ends_with_a_terminal_status(client):
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
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


@pytest.mark.timeout(20)
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
        with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
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


@pytest.mark.timeout(20)
def test_events_stream_disconnect_removes_subscriber(client, monkeypatch):
    # A client that stops reading (closes the connection, navigates away)
    # must not leave its subscriber queue registered forever either.
    #
    # This can't be exercised through TestClient: per its own source
    # (starlette.testclient's `send` callback writes every chunk into an
    # in-memory BytesIO, and `handle_request` doesn't return until the
    # whole ASGI call - i.e. the whole SSE stream - has finished), it
    # always waits for the entire response before handing back anything,
    # so there is no way to "read one line and stop" from a genuinely
    # still-open stream through it; reading only the first line only
    # looks like a disconnect, and passes even with disconnect handling
    # entirely removed, because the stream had already finished by then.
    #
    # A real disconnect, per sse_starlette's own internals, cancels the
    # task that is awaiting body_iterator's `__anext__()` (see the
    # `_ping` docstring in sse_starlette/sse.py for its own description of
    # this). Reproduce that directly against the endpoint's generator, on
    # a job that is provably still running so the cancellation lands on a
    # genuinely in-progress wait rather than an already-finished stream.
    _register_slow_command(monkeypatch)
    state = client.app.state.reshelf
    job_id = client.post("/api/jobs", json={"command": "slow", "args": {}}).json()[
        "job_id"
    ]

    async def disconnect_mid_stream():
        response = await job_events(job_id, state=state)
        gen = response.body_iterator
        await gen.__anext__()  # the initial snapshot: job is genuinely running
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)  # let it actually start awaiting the next event
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(disconnect_mid_stream())
    _wait_for_no_subscribers(state, job_id)
    # The disconnect only ended this one subscriber's stream, not the job
    # itself - let the still-running "slow" job wind down quickly instead
    # of tying up the worker (and this test's teardown) for its full 2s.
    state.runner.cancel(job_id)


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


# -- /api/plans and /api/journals ----------------------------------------
#
# The commit gate (Task 24) only makes sense if the UI can show the user a
# real plan/journal before they confirm - these cover the two read-only
# endpoints that make that possible.


def _make_plan(client):
    state = client.app.state.reshelf
    db, store = state.db, state.store
    fid, _ = db.upsert_file("/x/tbp.epub", 10, 1, "epub")
    db.set_hash(fid, "aaa")
    db.set_file_match(fid, None, 0.99, "MATCHED")
    book = Book(sha256="aaa", files=[FileEntry(path="/x/tbp.epub", format="epub")])
    book.metadata.title = "The Three-Body Problem"
    book.metadata.authors = ["Liu Cixin"]
    book.metadata.pubdate = "2014"
    store.save(book, "/x/tbp.epub")
    db.conn.commit()
    out = generate_plan(db, store, state.root / "reports")
    return out.name.removeprefix("plan-").removesuffix(".json")


def test_get_an_unknown_plan_is_404(client):
    assert client.get("/api/plans/does-not-exist").status_code == 404


def test_get_plan_refuses_an_id_crafted_to_escape_reports_dir(client):
    # FastAPI's default `str` path converter already refuses a literal '/'
    # in {plan_id} over HTTP, so this can't be reached through routing today
    # - but that's a property of routing, not of get_plan itself. Call the
    # handler directly (as test_events_stream_disconnect_removes_subscriber
    # already does elsewhere in this file) so resolve_inside_root's
    # containment check is pinned regardless of what routing allows.
    state = client.app.state.reshelf
    with pytest.raises(HTTPException) as exc:
        get_plan("../../../../../../etc/passwd", state=state)
    assert exc.value.status_code == 404


def test_get_journal_refuses_an_id_crafted_to_escape_reports_dir(client):
    state = client.app.state.reshelf
    with pytest.raises(HTTPException) as exc:
        get_journal("../../../../../../etc/passwd", state=state)
    assert exc.value.status_code == 404


def test_get_plan_annotates_actions_with_their_destination(client):
    plan_id = _make_plan(client)
    body = client.get(f"/api/plans/{plan_id}").json()
    assert body["plan_id"] == plan_id
    [action] = body["actions"]
    assert action["action"] == "import"
    assert action["dest"].endswith("Liu Cixin/The Three-Body Problem (2014)/The Three-Body Problem.epub")


def test_get_plan_dest_matches_what_a_real_commit_would_produce_on_a_collision(client):
    # dest_for alone would show the un-suffixed path here - the preview must
    # run the same unique_dest collision logic apply_plan does, or it shows
    # the user a destination the commit will not actually use.
    state = client.app.state.reshelf
    plan_id = _make_plan(client)
    dest = (
        state.root / "library" / "Liu Cixin" / "The Three-Body Problem (2014)"
        / "The Three-Body Problem.epub"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"a different file already lives here")  # sha256 mismatch forces a suffix

    body = client.get(f"/api/plans/{plan_id}").json()
    [action] = body["actions"]
    assert action["dest"] != str(dest)
    assert action["dest"] == str(dest.with_name("The Three-Body Problem-aaa.epub"))


def test_journals_list_is_empty_with_no_commits(client):
    assert client.get("/api/journals").json() == []


def _write_journal(root, commit_id, created_at, n_actions=1):
    journal = {
        "commit_id": commit_id,
        "created_at": created_at,
        "actions": [
            {"action": "import", "src": f"/x/{i}.epub", "dest": f"/lib/{i}.epub"}
            for i in range(n_actions)
        ],
        "skipped": [],
    }
    (root / "reports" / f"commit-{commit_id}.json").write_text(json.dumps(journal))


def test_journals_lists_newest_first_with_action_counts(client):
    state = client.app.state.reshelf
    _write_journal(state.root, "older", "2024-01-01T00:00:00+00:00", n_actions=1)
    _write_journal(state.root, "newer", "2024-06-01T00:00:00+00:00", n_actions=3)
    body = client.get("/api/journals").json()
    assert [j["commit_id"] for j in body] == ["newer", "older"]
    assert body[0]["actions"] == 3
    assert body[1]["actions"] == 1


def test_get_an_unknown_journal_is_404(client):
    assert client.get("/api/journals/does-not-exist").status_code == 404


def test_get_journal_returns_its_full_contents(client):
    state = client.app.state.reshelf
    _write_journal(state.root, "abc123", "2024-06-01T00:00:00+00:00", n_actions=2)
    body = client.get("/api/journals/abc123").json()
    assert body["commit_id"] == "abc123"
    assert len(body["actions"]) == 2


# -- I2: the preview must describe the commit that will actually run ------


def _make_unresolved_plan(client):
    """A plan holding one import and one quarantine action - the real
    library's shape, where quarantine rows outnumber imports 3:1."""
    state = client.app.state.reshelf
    db, store = state.db, state.store
    for path, sha, status, title in [
        ("/x/tbp.epub", "aaa", "MATCHED", "The Three-Body Problem"),
        ("/x/mystery.pdf", "bbb", "UNRESOLVED", None),
    ]:
        fid, _ = db.upsert_file(path, 10, 1, path.rsplit(".", 1)[1])
        db.set_hash(fid, sha)
        db.set_file_match(fid, None, 0.99, status)
        book = Book(sha256=sha, files=[FileEntry(path=path, format="epub")])
        if title:
            book.metadata.title = title
            book.metadata.authors = ["Liu Cixin"]
            book.metadata.pubdate = "2014"
        store.save(book, path)
    db.conn.commit()
    out = generate_plan(db, store, state.root / "reports")
    return out.name.removeprefix("plan-").removesuffix(".json")


def test_plan_preview_marks_the_actions_a_plain_commit_will_not_perform(client):
    """apply_plan skips every quarantine/mark_duplicate action unless the
    commit is given the matching flag, and the UI does not pass them - so
    the preview used to promise ~74% moves that never happened."""
    plan_id = _make_unresolved_plan(client)
    actions = client.get(f"/api/plans/{plan_id}").json()["actions"]
    by_kind = {a["action"]: a for a in actions}

    assert by_kind["import"]["will_apply"] is True
    assert by_kind["import"]["dest"]

    assert by_kind["quarantine"]["will_apply"] is False
    # and no destination is promised for something that will not move
    assert "dest" not in by_kind["quarantine"]


def test_plan_preview_honours_the_same_flags_the_commit_takes(client):
    plan_id = _make_unresolved_plan(client)
    actions = client.get(f"/api/plans/{plan_id}?quarantine=true").json()["actions"]
    quarantine = next(a for a in actions if a["action"] == "quarantine")
    assert quarantine["will_apply"] is True
    assert quarantine["dest"].endswith("/quarantine/mystery.pdf")


# -- I3: the commands that move files honour the previews' containment ----


def test_commit_refuses_a_plan_path_outside_the_reports_dir(client, tmp_path):
    """/plans/{id} routes through resolve_inside_root; the commit that
    actually applies a plan took Path(args["plan"]) raw."""
    outside = tmp_path.parent / "evil-plan.json"
    outside.write_text(json.dumps({"plan_id": "evil", "actions": []}))
    job_id = client.post(
        "/api/jobs",
        json={"command": "commit", "args": {"confirmed": True, "plan": str(outside)}},
    ).json()["job_id"]
    job = wait(client, job_id)
    assert job["status"] == "failed"
    assert "no such plan" in job["error"]


def test_rollback_refuses_a_journal_that_resolves_outside_the_reports_dir(
    client, tmp_path
):
    """A journal is a list of paths rollback will shutil.move. Loading one
    from outside reports/ hands whoever wrote it that move list.

    Two ways in: a `..` in the id (blocked today only by FastAPI's path
    converter, and only over HTTP), and a symlink under reports/, which
    nothing checked at all - Path.exists() happily follows it.
    """
    victim = tmp_path / "incoming" / "keep-me.epub"
    victim.write_bytes(b"MINE")
    outside = tmp_path.parent / "evil-journal.json"
    outside.write_text(
        json.dumps(
            {
                "commit_id": "evil",
                "actions": [
                    {
                        "action": "quarantine",
                        "src": str(tmp_path.parent / "stolen.epub"),
                        "dest": str(victim),
                    }
                ],
                "skipped": [],
            }
        )
    )
    (tmp_path / "reports" / "commit-sneaky.json").symlink_to(outside)

    job_id = client.post(
        "/api/jobs",
        json={"command": "rollback", "args": {"confirmed": True, "commit_id": "sneaky"}},
    ).json()["job_id"]
    job = wait(client, job_id)
    assert job["status"] == "failed"
    assert "no journal" in job["error"]
    assert victim.exists()  # the move the outside journal asked for never ran
    assert not (tmp_path.parent / "stolen.epub").exists()


def test_scan_refuses_a_path_outside_the_library_root(client, tmp_path):
    job_id = client.post(
        "/api/jobs", json={"command": "scan", "args": {"path": "/etc"}}
    ).json()["job_id"]
    job = wait(client, job_id)
    assert job["status"] == "failed"
    assert "no such directory" in job["error"]


def test_scan_still_accepts_a_directory_inside_the_root(client, tmp_path):
    job_id = client.post(
        "/api/jobs", json={"command": "scan", "args": {"path": str(tmp_path / "incoming")}}
    ).json()["job_id"]
    assert wait(client, job_id)["status"] == "done"
