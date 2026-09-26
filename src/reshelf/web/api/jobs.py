import asyncio
import contextlib
import json
import threading

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from reshelf.web.deps import AppState, get_state
from reshelf.web.jobs import TERMINAL, UnknownCommand
from reshelf.web.schemas import JobCreate

router = APIRouter(tags=["jobs"])

# These move or delete files. The UI must run `plan`, show the diff and
# confirm before it may enqueue them.
NEEDS_CONFIRMATION = {"commit", "rollback"}

# How long to wait for the next event before polling the job row directly.
# JobRunner.events() blocks on queue.Queue.get() with no timeout, and the
# terminal notify it depends on can be silently swallowed (see
# JobRunner._finish). Re-checking the DB on every idle tick means a missed
# notify still ends the stream - it just takes up to this long instead of
# forever - and doubles as an SSE keepalive so proxies don't drop the
# connection while a job is slow between progress ticks.
POLL_TIMEOUT = 2.0


@router.post("/jobs", status_code=202)
def create_job(payload: JobCreate, state: AppState = Depends(get_state)) -> dict:
    if payload.command in NEEDS_CONFIRMATION and not payload.args.get("confirmed"):
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
        # events() is a synchronous, blocking generator (queue.Queue.get()
        # with no timeout) - it must never run directly on the event loop,
        # or one slow job freezes every other request. Pump it from a
        # single dedicated daemon thread (not the loop's default executor:
        # if the hazard below ever leaves that thread permanently blocked,
        # asyncio's default-executor shutdown joins it and hangs the whole
        # process at exit - a daemon thread doesn't). Only this thread ever
        # calls next() on the generator, so there is never a concurrent
        # call into it.
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
            # between next() calls, not mid-block inside one. If a client
            # disconnects while the pump is still waiting on the next
            # event, the generator is "already executing" in that thread
            # and closing it here would raise - in that case it closes
            # itself (and removes its subscriber entry) the next time it
            # wakes, via the pump thread's own for-loop exiting normally.
            with contextlib.suppress(ValueError):
                iterator.close()

    return EventSourceResponse(stream())
