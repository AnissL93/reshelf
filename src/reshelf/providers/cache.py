import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


EMPTY_TTL = timedelta(days=1)


class FileCache:
    def __init__(self, directory: Path, ttl_days: int = 30):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(days=ttl_days)

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^a-z0-9._-]+", "_", key.lower())[:80]
        digest = hashlib.sha1(key.encode()).hexdigest()[:10]
        return self.directory / f"{safe}-{digest}.json"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        rec = json.loads(p.read_text())
        # An empty answer may be a throttled one (Douban answers [] when it
        # rate-limits), so it only stands for a day, not the full TTL.
        ttl = self.ttl if rec["response"] else min(self.ttl, EMPTY_TTL)
        if datetime.fromisoformat(rec["retrieved_at"]) < datetime.now(timezone.utc) - ttl:
            return None
        return rec["response"]

    def put(self, key: str, response) -> None:
        self._path(key).write_text(
            json.dumps(
                {
                    "query": key,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "response": response,
                },
                ensure_ascii=False,
            )
        )
