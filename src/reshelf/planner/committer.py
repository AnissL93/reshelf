import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from reshelf.db.database import Database
from reshelf.scanner.hashing import sha256_file


def _safe(component: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", component).strip(" .")
    return s[:80] or "_"


def dest_for(action: dict, library_dir: Path) -> Path:
    md = action.get("metadata_changes", {})
    title = _safe(md.get("title") or Path(action["file"]).stem)
    author = _safe(md.get("author") or "Unknown Author")
    m = re.search(r"\d{4}", md.get("publication_date") or "")
    dirname = f"{title} ({m.group()})" if m else title
    ext = Path(action["file"]).suffix.lower()
    return Path(library_dir) / author / dirname / f"{title}{ext}"


def verify_preconditions(action: dict) -> str | None:
    """Return a skip reason if the file changed since planning, else None."""
    src = Path(action["file"])
    pre = action["preconditions"]
    if not src.exists():
        return "source missing"
    st = src.stat()
    if st.st_size != pre["size"] or int(st.st_mtime) != pre["mtime"]:
        return "changed since plan (size/mtime)"
    if pre.get("sha256") and sha256_file(src) != pre["sha256"]:
        return "changed since plan (sha256)"
    return None


def unique_dest(dest: Path, sha256: str | None) -> tuple[Path, bool]:
    """Resolve collisions. Returns (dest, already_done)."""
    if not dest.exists():
        return dest, False
    if sha256 and sha256_file(dest) == sha256:
        return dest, True  # identical copy already in place
    suffix = (sha256 or uuid4().hex)[:8]
    return dest.with_name(f"{dest.stem}-{suffix}{dest.suffix}"), False


def apply_plan(
    plan: dict,
    db: Database,
    library_dir: Path,
    quarantine_dir: Path,
    duplicates_dir: Path,
    reports_dir: Path,
    dry_run: bool = False,
    do_quarantine: bool = False,
    do_duplicates: bool = False,
    mode: str = "copy",
) -> dict:
    now = datetime.now(timezone.utc)
    commit_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    done: list[dict] = []
    skipped: list[dict] = []

    def skip(action: dict, reason: str) -> None:
        skipped.append({"file": action["file"], "action": action["action"], "reason": reason})

    def move_into(action: dict, target_dir: Path) -> None:
        src = Path(action["file"])
        dest, already = unique_dest(
            Path(target_dir) / src.name, action["preconditions"].get("sha256")
        )
        if already:
            skip(action, "already in place")
            return
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dest)
            db.conn.execute(
                "UPDATE files SET path=? WHERE path=?", (str(dest), str(src))
            )
            db.conn.commit()
        done.append({"action": action["action"], "src": str(src), "dest": str(dest)})

    try:
        for action in plan["actions"]:
            kind = action["action"]
            if kind == "import":
                row = db.conn.execute(
                    "SELECT id, status FROM files WHERE path=?", (action["file"],)
                ).fetchone()
                if row is None:
                    skip(action, "not in database")
                    continue
                if row["status"] == "COMMITTED":
                    skip(action, "already committed")
                    continue
                reason = verify_preconditions(action)
                if reason:
                    skip(action, reason)
                    continue
                dest, already = unique_dest(
                    dest_for(action, library_dir),
                    action["preconditions"].get("sha256"),
                )
                if not dry_run:
                    if not already:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if mode == "move":
                            shutil.move(action["file"], dest)
                        else:
                            shutil.copy2(action["file"], dest)
                    db.set_status(row["id"], "COMMITTED")
                    db.conn.commit()
                done.append(
                    {
                        "action": "import",
                        "src": action["file"],
                        "dest": str(dest),
                        "moved": mode == "move",
                    }
                )
            elif kind == "quarantine":
                if not do_quarantine:
                    skip(action, "quarantine disabled (pass --quarantine)")
                    continue
                reason = verify_preconditions(action)
                if reason:
                    skip(action, reason)
                    continue
                move_into(action, quarantine_dir)
            elif kind == "mark_duplicate":
                if not do_duplicates:
                    skip(action, "duplicate handling disabled (pass --duplicates)")
                    continue
                reason = verify_preconditions(action)
                if reason:
                    skip(action, reason)
                    continue
                move_into(action, duplicates_dir)
            else:
                skip(action, f"unknown action {kind}")
    finally:
        journal = {
            "commit_id": commit_id,
            "plan_id": plan.get("plan_id"),
            "created_at": now.isoformat(),
            "dry_run": dry_run,
            "actions": done,
            "skipped": skipped,
        }
        if not dry_run:
            reports_dir = Path(reports_dir)
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / f"commit-{commit_id}.json").write_text(
                json.dumps(journal, ensure_ascii=False, indent=2)
            )
    return journal


def _prune_empty(parent: Path, library_dir: Path) -> None:
    while parent != Path(library_dir) and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def rollback_journal(journal: dict, db: Database, library_dir: Path) -> dict:
    reverted: list[dict] = []
    skipped: list[dict] = []
    for entry in reversed(journal.get("actions", [])):
        src, dest = Path(entry["src"]), Path(entry["dest"])
        if entry["action"] == "import":
            if entry.get("moved"):
                if not dest.exists() or src.exists():
                    skipped.append({**entry, "reason": "cannot restore"})
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), src)
                _prune_empty(dest.parent, library_dir)
            elif dest.exists():
                dest.unlink()
                _prune_empty(dest.parent, library_dir)
            db.conn.execute(
                "UPDATE files SET status='MATCHED' WHERE path=? AND status='COMMITTED'",
                (str(src),),
            )
            reverted.append(entry)
        else:  # quarantine / mark_duplicate were moves
            if dest.exists() and not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), src)
                db.conn.execute(
                    "UPDATE files SET path=? WHERE path=?", (str(src), str(dest))
                )
                reverted.append(entry)
            else:
                skipped.append({**entry, "reason": "cannot restore"})
    db.conn.commit()
    return {"reverted": len(reverted), "skipped": len(skipped)}
