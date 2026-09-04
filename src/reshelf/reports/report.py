from reshelf.db.database import Database


def build_report(db: Database) -> dict:
    def one(sql: str) -> int:
        return db.conn.execute(sql).fetchone()[0]

    return {
        "files_scanned": one("SELECT COUNT(*) FROM files"),
        "exact_isbn_matches": one(
            "SELECT COUNT(DISTINCT file_id) FROM matches"
            " WHERE evidence_json LIKE '%\"exact_isbn\"%'"
        ),
        "high_confidence": one(
            "SELECT COUNT(DISTINCT file_id) FROM matches WHERE status IN"
            " ('AUTO_ACCEPT','HIGH_CONFIDENCE')"
            " AND evidence_json NOT LIKE '%\"exact_isbn\"%'"
        ),
        "needs_review": one("SELECT COUNT(*) FROM files WHERE status='REVIEW'"),
        "duplicates": one("SELECT COUNT(*) FROM files WHERE status='DUPLICATE'"),
        "unresolved": one("SELECT COUNT(*) FROM files WHERE status='UNRESOLVED'"),
        "errors": one("SELECT COUNT(*) FROM files WHERE status='ERROR'"),
    }
