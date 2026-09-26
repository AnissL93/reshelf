"""Path containment, in one place.

Three layers need to ask "is this path inside that directory?" and they
must all answer it the same way:

* is this `book.files` entry the committed copy under `<root>/library`
  (writeback's rename source, `Book.primary_file`'s ranking, and the guard
  that stops tier-3 embedding from rewriting an original under `incoming/`)?
* is this caller-supplied plan/journal/file path inside the library root?

A bare component test (`"library" in Path(p).parts`) answers the first
question wrong for any user whose library root is itself named `library`
- `/mnt/d/library/incoming/foo.epub` is an *original*, not a library copy.
Resolve and compare, always.
"""

from pathlib import Path


def resolve_inside(root: Path, candidate: str | Path) -> Path | None:
    """`candidate` resolved, or None if it escapes `root`.

    A relative candidate is taken as relative to `root`. Path.resolve() on
    both sides collapses symlinks and '..' before the comparison, so a
    symlink under root pointing outside it is caught too - and so is an id
    crafted to contain '/' or '..'. Existence is not required (see
    `resolve_inside_root` for that).
    """
    path = Path(candidate)
    if not path.is_absolute():
        path = Path(root) / path
    try:
        path = path.resolve()
        path.relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return None
    return path


def resolve_inside_root(root: Path, candidate: str | Path) -> Path | None:
    """`resolve_inside`, but also requires the result to be an existing file.

    Used wherever the next thing that happens is opening the file:
    books.py (a sidecar's file path - user-editable JSON, so it can point
    anywhere) and jobs.py (a plan/journal id taken straight off a URL path
    parameter, or out of a job's args).
    """
    path = resolve_inside(root, candidate)
    return path if path is not None and path.is_file() else None


def under(path: str | Path, directory: str | Path | None) -> bool:
    """True when `path` resolves to somewhere inside `directory`.

    `directory=None` means "no such directory is known here", which is
    never inside anything - callers that cannot supply one (index.sync has
    no config) get "not a library copy" rather than a wrong guess.

    Unlike `resolve_inside`, a relative `path` is resolved against the
    process cwd, never against `directory` - a `book.files` entry reading
    "incoming/x.epub" is not a library copy just because it was compared
    against the library dir.
    """
    if directory is None:
        return False
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
    except (OSError, ValueError):
        return False
    return True
