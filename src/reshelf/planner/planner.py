import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from reshelf.db.database import Database
from reshelf.store.sidecar import SidecarStore


def _preconditions(row) -> dict:
    return {"sha256": row["sha256"], "size": row["size"], "mtime": row["mtime"]}


def generate_plan(db: Database, store: SidecarStore, reports_dir: Path) -> Path:
    now = datetime.now(timezone.utc)
    plan_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    actions: list[dict] = []

    matched = db.conn.execute(
        "SELECT * FROM files WHERE status = 'MATCHED' ORDER BY path"
    ).fetchall()
    for r in matched:
        book = store.load(r["sha256"], r["path"]) if r["sha256"] else None
        if book is None:
            continue  # no sidecar means no metadata to name a destination by
        m = book.metadata
        actions.append(
            {
                "file": r["path"],
                "action": "import",
                "preconditions": _preconditions(r),
                "metadata_changes": {
                    "title": m.title,
                    "author": "; ".join(m.authors) or None,
                    "isbn13": m.isbn13,
                    "publisher": m.publisher,
                    "publication_date": m.pubdate,
                },
            }
        )
    for status, action in [("DUPLICATE", "mark_duplicate"), ("UNRESOLVED", "quarantine")]:
        for r in db.files_with_status(status):
            actions.append(
                {"file": r["path"], "action": action, "preconditions": _preconditions(r)}
            )

    plan = {"plan_id": plan_id, "created_at": now.isoformat(), "actions": actions}
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"plan-{plan_id}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    return out
