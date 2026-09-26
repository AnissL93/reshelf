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
