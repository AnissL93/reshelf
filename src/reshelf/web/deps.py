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
