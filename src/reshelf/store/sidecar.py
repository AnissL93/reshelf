"""Atomic, locked read/write of sidecar documents."""

import json
import logging
import os
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

from reshelf.config import Config
from reshelf.store.models import Book, now

logger = logging.getLogger(__name__)


class SidecarStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = Path(cfg.library.root).resolve()
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock(self, sha256: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks[sha256]

    def path_for(self, sha256: str, file_path: str | None = None) -> Path:
        layout = self.cfg.metadata.layout
        if layout == "hash":
            return self.root / self.cfg.metadata.dir / f"{sha256}.json"
        if file_path is None:
            raise ValueError(f"metadata.layout={layout!r} needs a file_path")
        p = Path(file_path)
        if not p.is_absolute():
            p = self.root / p
        return p.with_name(p.name + ".json")

    def load(self, sha256: str, file_path: str | None = None) -> Book | None:
        try:
            path = self.path_for(sha256, file_path)
        except ValueError:
            # A non-hash layout with no file to key on (a hashed row whose
            # `files` rows are gone, say). There is no sidecar to find, and
            # every caller of load() already handles None - raising here
            # instead turned "not found" into a 500.
            return None
        if not path.exists():
            return None
        return Book.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, book: Book, file_path: str | None = None) -> Path:
        path = self.path_for(book.sha256, file_path)
        with self._lock(book.sha256):
            return self._write(path, book)

    def _write(self, path: Path, book: Book) -> Path:
        book.updated_at = now()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            book.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        return path

    def update(
        self,
        sha256: str,
        mutate: Callable[[Book], None],
        file_path: str | None = None,
    ) -> Book:
        """Load, mutate and write back under one lock."""
        path = self.path_for(sha256, file_path)
        with self._lock(sha256):
            if not path.exists():
                raise KeyError(sha256)
            book = Book.model_validate(json.loads(path.read_text(encoding="utf-8")))
            mutate(book)
            self._write(path, book)
            return book

    def iter_all(self) -> Iterator[Book]:
        if self.cfg.metadata.layout == "hash":
            paths = sorted((self.root / self.cfg.metadata.dir).glob("*.json"))
        else:
            paths = sorted(self.root.rglob("*.json"))
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("skipping unreadable sidecar %s: %s", path, exc)
                continue
            if isinstance(data, dict) and "sha256" in data:
                yield Book.model_validate(data)

    def delete(self, sha256: str, file_path: str | None = None) -> None:
        with self._lock(sha256):
            self.path_for(sha256, file_path).unlink(missing_ok=True)
