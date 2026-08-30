from pathlib import Path

import typer

from book_organizer.config import default_config, save_config
from book_organizer.db.database import Database

app = typer.Typer(no_args_is_help=True)

SUBDIRS = [
    "incoming", "library", "quarantine", "duplicates", "covers",
    "cache/openlibrary", "reports", "db",
]


@app.callback()
def cli() -> None:
    """Organize large collections of EPUB/PDF ebooks."""


@app.command()
def init(root: Path) -> None:
    """Create the directory layout, config.yaml, and database."""
    root = root.resolve()
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = default_config(root)
    if not (root / "config.yaml").exists():
        save_config(cfg, root)
    with Database(cfg.database.path) as db:
        db.init_schema()
    typer.echo(f"initialized {root}")


def main() -> None:
    app()
