import asyncio
import contextlib
import json
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from reshelf.planner.committer import dest_for, unique_dest
from reshelf.web.deps import AppState, get_state, resolve_inside_root
from reshelf.web.jobs import TERMINAL, UnknownCommand
from reshelf.web.schemas import JobCreate

router = APIRouter(tags=["jobs"])

# These move or delete files. The UI must run `plan`, show the diff and
# confirm before it may enqueue them.
NEEDS_CONFIRMATION = {"commit", "rollback"}

# How long to wait for the next event before polling the job row directly.
# JobRunner.events() already self-heals a swallowed terminal publish on its
# own (see EVENTS_POLL_TIMEOUT in reshelf.web.jobs) - this is a slower
# backstop on top of that, in case something ever delays the generator
# itself, and it doubles as an SSE keepalive so proxies don't drop an idle
# connection.
POLL_TIMEOUT = 3.0


@router.post("/jobs", status_code=202)
def create_job(payload: JobCreate, state: AppState = Depends(get_state)) -> dict:
    # Strict identity, not truthiness: args comes straight from raw JSON with
    # no coercion (JobCreate.args is dict[str, Any]), and a client that
    # serializes a checkbox as "false" or "0" would otherwise sail through -
    # every non-empty string is truthy in Python. commit/rollback move and
    # delete the user's files, so only the literal boolean True may pass.
    confirmed = payload.args.get("confirmed")
    if payload.command in NEEDS_CONFIRMATION and confirmed is not True:
        raise HTTPException(
            409,
            f"{payload.command} needs an explicit confirmation:"
            " run plan, show the diff, then resend with args.confirmed = true",
        )
    try:
        job_id = state.runner.enqueue(payload.command, payload.args)
    except UnknownCommand as e:
        raise HTTPException(422, f"unknown command: {e}") from e
    return {"job_id": job_id}


@router.get("/jobs")
def list_jobs(limit: int = 50, state: AppState = Depends(get_state)) -> list[dict]:
    return state.runner.recent(limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, state: AppState = Depends(get_state)) -> dict:
    job = state.runner.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: int, state: AppState = Depends(get_state)) -> dict:
    if state.runner.get(job_id) is None:
        raise HTTPException(404, "no such job")
    return {"cancelled": state.runner.cancel(job_id)}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: int, state: AppState = Depends(get_state)):
    if state.runner.get(job_id) is None:
        raise HTTPException(404, "no such job")

    async def stream():
        # events() is a synchronous generator (it waits on queue.Queue.get())
        # - it must never run directly on the event loop, or one slow job
        # freezes every other request. Pump it from a single dedicated
        # daemon thread, not the loop's default executor: events() bounds
        # its own wait (EVENTS_POLL_TIMEOUT) so it no longer blocks forever,
        # but if it ever did, asyncio's default-executor shutdown joins a
        # stuck worker and hangs the whole process at exit - a plain daemon
        # thread doesn't. Only this thread ever calls next() on the
        # generator, so there is never a concurrent call into it.
        iterator = state.runner.events(job_id)
        loop = asyncio.get_running_loop()
        out: asyncio.Queue = asyncio.Queue()

        def pump() -> None:
            try:
                for event in iterator:
                    try:
                        loop.call_soon_threadsafe(out.put_nowait, event)
                    except RuntimeError:
                        return  # the event loop is already gone
                    if event["status"] in TERMINAL:
                        return
            finally:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(out.put_nowait, None)

        threading.Thread(target=pump, daemon=True, name=f"sse-events-{job_id}").start()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(out.get(), timeout=POLL_TIMEOUT)
                except asyncio.TimeoutError:
                    # No event within the timeout - ask the DB directly.
                    # This is the self-heal: if the terminal publish was
                    # ever swallowed, the row is still terminal, and we
                    # notice it here instead of waiting on a queue that
                    # will never be fed again. Doubles as an SSE keepalive
                    # so proxies don't drop an idle connection.
                    snapshot = state.runner.get(job_id)
                    if snapshot is not None and snapshot["status"] in TERMINAL:
                        yield {"data": json.dumps(snapshot, default=str)}
                        return
                    yield {"comment": "keepalive"}
                    continue
                if event is None:
                    return
                yield {"data": json.dumps(event, default=str)}
                if event["status"] in TERMINAL:
                    return
        finally:
            # Best effort: this is only safe when the pump thread is
            # between next() calls, not mid-block inside one - in that
            # instant the generator is "already executing" and closing it
            # here would raise. Since events() now wakes on its own at
            # least every EVENTS_POLL_TIMEOUT, that race window is bounded
            # (not permanent): if we lose it, the generator closes itself
            # and removes its subscriber entry the next time it wakes.
            with contextlib.suppress(ValueError):
                iterator.close()

    return EventSourceResponse(stream())


# -- plan / journal previews (reports/*.json the pipeline already writes) --
#
# Neither file is a job result you can just read off the job row: a `plan`
# job's `message` is only the plan's path, and a `commit` job's args only
# name a journal by id. The commit gate (NEEDS_CONFIRMATION above) is only
# real if the UI can show the user what a plan/journal actually contains
# before they confirm - these two GETs exist for exactly that, and touch
# nothing.
#
# `plan_id`/`commit_id` are caller-supplied path parameters used to build a
# filename. FastAPI's default `str` converter already refuses a `/` in
# them, but that safety is a property of routing, not of this code - it
# would evaporate behind a reverse proxy that forwards an un-normalised
# path, or if the route ever grows a `:path` converter. resolve_inside_root
# (shared with books.py's file-serving guard) is the actual containment
# check, done here regardless of what routing currently allows.


def _reports_dir(state: AppState) -> Path:
    return Path(state.cfg.library.root) / "reports"


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, state: AppState = Depends(get_state)) -> dict:
    path = resolve_inside_root(_reports_dir(state), f"plan-{plan_id}.json")
    if path is None:
        raise HTTPException(404, "no such plan")
    plan = json.loads(path.read_text())
    # Annotate each action with the destination the real commit would use,
    # so the preview can show real `src -> dest` rows rather than just the
    # source file. This calls the exact same functions apply_plan itself
    # calls (dest_for, then unique_dest for the collision suffix) so the
    # preview and a real commit can never disagree about where a file will
    # land. unique_dest only stats (and, on a same-named collision, hashes)
    # an existing file - read-only, so a plan can be previewed any number of
    # times with no side effects.
    root = Path(state.cfg.library.root)
    targets = {"quarantine": state.cfg.library.quarantine, "mark_duplicate": root / "duplicates"}
    for action in plan.get("actions", []):
        kind = action.get("action")
        sha256 = (action.get("preconditions") or {}).get("sha256")
        if kind == "import":
            dest, _ = unique_dest(dest_for(action, root / "library"), sha256)
            action["dest"] = str(dest)
        elif kind in targets:
            dest, _ = unique_dest(targets[kind] / Path(action["file"]).name, sha256)
            action["dest"] = str(dest)
    return plan


@router.get("/journals")
def list_journals(state: AppState = Depends(get_state)) -> list[dict]:
    reports = _reports_dir(state)
    out = []
    for path in reports.glob("commit-*.json") if reports.exists() else []:
        try:
            journal = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue  # a partially-written or unreadable file; skip it silently
        out.append(
            {
                "commit_id": journal.get("commit_id", path.stem.removeprefix("commit-")),
                "created_at": journal.get("created_at"),
                "actions": len(journal.get("actions", [])),
            }
        )
    out.sort(key=lambda j: j["created_at"] or "", reverse=True)
    return out


@router.get("/journals/{commit_id}")
def get_journal(commit_id: str, state: AppState = Depends(get_state)) -> dict:
    path = resolve_inside_root(_reports_dir(state), f"commit-{commit_id}.json")
    if path is None:
        raise HTTPException(404, "no such journal")
    return json.loads(path.read_text())
