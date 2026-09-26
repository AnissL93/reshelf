"""One Database, one SidecarStore, one JobRunner for the process.

Database takes an exclusive lock file, so the server owns exactly one and
shares it with the worker thread. A second reshelf process (including a
CLI command) will refuse to start while the server runs - that is the
intended one-writer guarantee.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from reshelf.config import Config, load_config, save_config
from reshelf.db.database import Database
from reshelf.store.sidecar import SidecarStore
from reshelf.web.jobs import JobRunner


@dataclass
class AppState:
    root: Path
    cfg: Config
    db: Database
    store: SidecarStore
    runner: JobRunner

    def reload_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store.cfg = cfg
        self.runner.cfg = cfg
        save_config(cfg, self.root)


def build_state(root: Path) -> AppState:
    root = Path(root).resolve()
    cfg = load_config(root)
    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    runner = JobRunner(cfg, db, store)
    return AppState(root=root, cfg=cfg, db=db, store=store, runner=runner)


def get_state(request: Request) -> AppState:
    return request.app.state.reshelf


def resolve_inside_root(root: Path, candidate: str) -> Path | None:
    """Never serve a path outside `root`, whatever the caller-supplied id/path says.

    Shared by books.py (a sidecar's file path - user-editable JSON, so it can
    point anywhere) and jobs.py (a plan/journal id taken straight off a URL
    path parameter). Path.resolve() on both sides collapses symlinks and
    '..' before the containment check, so a symlink under root pointing
    outside it is caught too - and, on the jobs.py side, so is an id crafted
    to contain '/' or '..' if this ever runs somewhere FastAPI's own path
    converter isn't already refusing a '/' in the parameter for it.
    """
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None
