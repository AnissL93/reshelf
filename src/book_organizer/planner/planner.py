import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from book_organizer.db.database import Database


def _preconditions(row) -> dict:
    return {"sha256": row["sha256"], "size": row["size"], "mtime": row["mtime"]}


def generate_plan(db: Database, reports_dir: Path) -> Path:
    now = datetime.now(timezone.utc)
    plan_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    actions: list[dict] = []

    matched = db.conn.execute(
        "SELECT f.*, e.isbn13, e.publisher, e.publication_date,"
        " w.canonical_title,"
        " (SELECT a.canonical_name FROM work_authors wa"
        "   JOIN authors a ON a.id = wa.author_id"
        "   WHERE wa.work_id = w.id LIMIT 1) AS author"
        " FROM files f"
        " JOIN editions e ON e.id = f.matched_edition_id"
        " JOIN works w ON w.id = e.work_id"
        " WHERE f.status = 'MATCHED' ORDER BY f.path"
    ).fetchall()
    for r in matched:
        actions.append(
            {
                "file": r["path"],
                "action": "import",
                "preconditions": _preconditions(r),
                "metadata_changes": {
                    "title": r["canonical_title"],
                    "author": r["author"],
                    "isbn13": r["isbn13"],
                    "publisher": r["publisher"],
                    "publication_date": r["publication_date"],
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
