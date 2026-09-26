"""One worker thread, one job at a time.

# ponytail: the single worker IS the lock. A real queue (RQ/Celery) only
# if concurrent pipeline stages are ever wanted; a single-user box does
# not want them.
"""

import json
import queue
import threading
import traceback
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from reshelf import pipeline
from reshelf.store.bootstrap import reindex as _reindex

TERMINAL = ("done", "failed", "cancelled", "interrupted")


class UnknownCommand(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_plan(cfg) -> Path:
    reports = Path(cfg.library.root) / "reports"
    plans = sorted(reports.glob("plan-*.json"))
    if not plans:
        raise RuntimeError("no plan found; run plan first")
    return plans[-1]


COMMANDS: dict[str, Callable] = {
    "scan": lambda cfg, db, store, args, progress: pipeline.scan(
        cfg, db, store, Path(args.get("path") or cfg.library.incoming), progress
    ),
    "extract": lambda cfg, db, store, args, progress: pipeline.extract(
        cfg, db, store, bool(args.get("force")), progress
    ),
    "match": lambda cfg, db, store, args, progress: pipeline.match(
        cfg, db, store, bool(args.get("offline")), progress
    ),
    "resolve": lambda cfg, db, store, args, progress: pipeline.resolve(
        cfg, db, store, int(args.get("limit") or 0),
        bool(args.get("include_unresolved")), progress
    ),
    "plan": lambda cfg, db, store, args, progress: str(
        pipeline.plan(cfg, db, store)
    ),
    "commit": lambda cfg, db, store, args, progress: pipeline.commit(
        cfg, db, store, Path(args["plan"]) if args.get("plan") else _latest_plan(cfg),
        progress,
        dry_run=bool(args.get("dry_run")),
        do_quarantine=bool(args.get("quarantine")),
        do_duplicates=bool(args.get("duplicates")),
    ),
    "rollback": lambda cfg, db, store, args, progress: pipeline.rollback(
        cfg, db, args["commit_id"], progress
    ),
    "reindex": lambda cfg, db, store, args, progress: _reindex(db, store, progress),
    "rematch": lambda cfg, db, store, args, progress: [
        c.model_dump()
        for c in pipeline.match_one(
            cfg, db, store, args["sha256"],
            query=args.get("query"), use_ai=bool(args.get("ai")),
        )
    ],
    "convert": lambda cfg, db, store, args, progress: pipeline.convert_book(
        cfg, db, store, args["sha256"], progress
    ),
}


class JobRunner:
    def __init__(self, cfg, db, store):
        self.cfg, self.db, self.store = cfg, db, store
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._cancels: dict[int, threading.Event] = {}
        self._subscribers: dict[int, list[queue.Queue]] = {}
        self._subs_guard = threading.Lock()

    # -- lifecycle ---------------------------------------------------

    def start(self) -> None:
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE jobs SET status='interrupted', finished_at=?"
                " WHERE status IN ('queued','running')",
                (_now(),),
            )
            self.db.conn.commit()
        self._thread = threading.Thread(target=self._work, daemon=True, name="jobs")
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(-1)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- api ---------------------------------------------------------

    def enqueue(self, command: str, args: dict | None = None) -> int:
        if command not in COMMANDS:
            raise UnknownCommand(command)
        with self.db.lock:
            cur = self.db.conn.execute(
                "INSERT INTO jobs (command, args_json, status, created_at)"
                " VALUES (?,?,'queued',?)",
                (command, json.dumps(args or {}), _now()),
            )
            self.db.conn.commit()
            job_id = cur.lastrowid
        self._queue.put(job_id)
        return job_id

    def get(self, job_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 50) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def cancel(self, job_id: int) -> bool:
        event = self._cancels.get(job_id)
        if event is None:
            with self.db.lock:
                changed = self.db.conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=?"
                    " WHERE id=? AND status='queued'",
                    (_now(), job_id),
                ).rowcount
                self.db.conn.commit()
            if changed:
                self._publish(job_id)
            return bool(changed)
        event.set()
        return True

    def events(self, job_id: int) -> Iterator[dict]:
        q: queue.Queue = queue.Queue()
        with self._subs_guard:
            self._subscribers.setdefault(job_id, []).append(q)
        try:
            snapshot = self.get(job_id)
            if snapshot:
                yield snapshot
                if snapshot["status"] in TERMINAL:
                    return
            while True:
                event = q.get()
                yield event
                if event["status"] in TERMINAL:
                    return
        finally:
            with self._subs_guard:
                subs = self._subscribers.get(job_id, [])
                if q in subs:
                    subs.remove(q)

    # -- internals ---------------------------------------------------

    def _publish(self, job_id: int) -> None:
        snapshot = self.get(job_id)
        if snapshot is None:
            return
        with self._subs_guard:
            for q in list(self._subscribers.get(job_id, [])):
                q.put(snapshot)

    def _progress_for(self, job_id: int, cancel: threading.Event):
        # Cancellation is cooperative: it only takes effect the next time
        # the running stage calls progress(). A stage that loops without
        # ever reporting progress cannot be cancelled - acceptable for a
        # single-user tool, since every pipeline stage here does report.
        def progress(done: int, total: int | None, message: str) -> None:
            if cancel.is_set():
                raise pipeline.JobCancelled()
            with self.db.lock:
                self.db.conn.execute(
                    "UPDATE jobs SET progress=?, total=?, message=?,"
                    " log = substr(COALESCE(log,'') || ? , -20000)"
                    " WHERE id=?",
                    (done, total, message, f"{message}\n", job_id),
                )
                self.db.conn.commit()
            self._publish(job_id)

        return progress

    def _finish(self, job_id: int, status: str, message: str = "", error: str = ""):
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE jobs SET status=?, message=?, error=?, finished_at=?"
                " WHERE id=?",
                (status, message, error, _now(), job_id),
            )
            self.db.conn.commit()
        self._publish(job_id)

    def _work(self) -> None:
        while not self._stopping.is_set():
            job_id = self._queue.get()
            if job_id == -1:
                return
            job = self.get(job_id)
            if job is None or job["status"] != "queued":
                continue

            cancel = threading.Event()
            self._cancels[job_id] = cancel
            with self.db.lock:
                self.db.conn.execute(
                    "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                    (_now(), job_id),
                )
                self.db.conn.commit()
            self._publish(job_id)

            try:
                result = COMMANDS[job["command"]](
                    self.cfg,
                    self.db,
                    self.store,
                    json.loads(job["args_json"] or "{}"),
                    self._progress_for(job_id, cancel),
                )
                self._finish(job_id, "done", json.dumps(result, default=str)[:4000])
            except pipeline.JobCancelled:
                self._finish(job_id, "cancelled")
            except Exception as e:
                # A pipeline stage can raise anything (network errors,
                # AIDisabledError, KeyError on a bad arg, ...). None of it
                # may kill this thread - the worker must keep serving the
                # next job in the queue.
                self._finish(
                    job_id, "failed", error=f"{e}\n{traceback.format_exc()[-2000:]}"
                )
            finally:
                self._cancels.pop(job_id, None)
