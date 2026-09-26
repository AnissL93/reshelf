import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.pipeline import JobCancelled, extract, scan
from reshelf.store.sidecar import SidecarStore
from tests.helpers import make_epub

NOOP = lambda *a: None  # noqa: E731


@pytest.fixture
def env(tmp_path):
    cfg = default_config(tmp_path)
    cfg.library.incoming.mkdir(parents=True)
    db = Database(cfg.database.path)
    db.init_schema()
    yield cfg, db, SidecarStore(cfg)
    db.close()


def test_scan_reports_counts(env):
    cfg, db, store = env
    make_epub(cfg.library.incoming / "dune.epub", "Dune", "Frank Herbert")
    result = scan(cfg, db, store, cfg.library.incoming, NOOP)
    assert result["seen"] == 1
    assert result["added"] == 1


def test_scan_calls_progress_for_each_file(env):
    cfg, db, store = env
    for n in range(3):
        make_epub(cfg.library.incoming / f"b{n}.epub", f"B{n}", "A")
    calls = []
    scan(cfg, db, store, cfg.library.incoming, lambda *a: calls.append(a))
    assert len(calls) == 3
    assert all(len(c) == 3 for c in calls)


def test_progress_may_cancel_a_run(env):
    cfg, db, store = env
    for n in range(5):
        make_epub(cfg.library.incoming / f"b{n}.epub", f"B{n}", "A")

    def cancel_after_two(done, total, message):
        if done > 2:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        scan(cfg, db, store, cfg.library.incoming, cancel_after_two)


def test_extract_writes_a_sidecar_and_an_index_row(env):
    cfg, db, store = env
    make_epub(cfg.library.incoming / "dune.epub", "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    result = extract(cfg, db, store, force=False, progress=NOOP)
    assert result["extracted"] == 1

    from reshelf.store import index

    rows, total = index.query(db.conn, q="Dune")
    assert total == 1
    assert rows[0]["title"] == "Dune"

    book = next(store.iter_all())
    assert book.metadata.title == "Dune"
    assert book.source.resolver == "embedded"


def test_extract_does_not_clobber_a_human_edit(env):
    cfg, db, store = env
    make_epub(cfg.library.incoming / "dune.epub", "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)

    sha = next(store.iter_all()).sha256
    store.update(sha, lambda b: (
        setattr(b.metadata, "title", "My Title"),
        setattr(b.source, "resolver", "human"),
    ))
    extract(cfg, db, store, force=True, progress=NOOP)
    assert store.load(sha).metadata.title == "My Title"


def test_extract_marks_unreadable_files_as_errors(env):
    cfg, db, store = env
    (cfg.library.incoming / "broken.epub").write_bytes(b"not a zip")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    result = extract(cfg, db, store, force=False, progress=NOOP)
    assert result["errors"] == 1


def test_a_changed_file_carries_its_sidecar_to_the_new_hash(env):
    cfg, db, store = env
    path = cfg.library.incoming / "dune.epub"
    make_epub(path, "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)

    old_sha = next(store.iter_all()).sha256
    store.update(old_sha, lambda b: (
        setattr(b.metadata, "title", "My Corrected Title"),
        setattr(b.source, "resolver", "human"),
    ))

    # Same path, different bytes - a better scan of the same book.
    import os
    make_epub(path, "Dune", "Frank Herbert")
    with open(path, "ab") as fh:
        fh.write(b"\x00" * 64)
    os.utime(path, (0, 0))

    scan(cfg, db, store, cfg.library.incoming, NOOP)

    books = {b.sha256: b for b in store.iter_all()}
    new_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path),)
    ).fetchone()[0]
    assert new_sha != old_sha
    assert books[new_sha].metadata.title == "My Corrected Title"
    assert books[new_sha].is_human


def _bump_bytes(path):
    import os

    with open(path, "ab") as fh:
        fh.write(b"\x00" * 64)
    os.utime(path, (0, 0))


def test_carry_sidecar_works_under_sidecar_layout(env):
    """path_for ignores the sha under 'sidecar' layout - the existence guard
    must not mistake the not-yet-rewritten old sidecar for an existing new
    one and no-op the whole carry."""
    cfg, db, _default_store = env
    cfg.metadata.layout = "sidecar"
    store = SidecarStore(cfg)
    path = cfg.library.incoming / "dune.epub"
    make_epub(path, "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)

    old_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path),)
    ).fetchone()[0]
    store.update(
        old_sha,
        lambda b: (
            setattr(b.metadata, "title", "My Corrected Title"),
            setattr(b.source, "resolver", "human"),
        ),
        str(path),
    )

    make_epub(path, "Dune", "Frank Herbert")
    _bump_bytes(path)

    scan(cfg, db, store, cfg.library.incoming, NOOP)

    new_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path),)
    ).fetchone()[0]
    assert new_sha != old_sha
    book = store.load(new_sha, str(path))
    assert book is not None
    assert book.sha256 == new_sha
    assert book.metadata.title == "My Corrected Title"
    assert book.is_human


def test_carry_sidecar_leaves_the_file_readable_under_sidecar_layout(env):
    """Under 'sidecar'/'library' layout the old and new paths are identical,
    so save-then-delete must not destroy the sidecar it just wrote."""
    cfg, db, _default_store = env
    cfg.metadata.layout = "sidecar"
    store = SidecarStore(cfg)
    path = cfg.library.incoming / "dune.epub"
    make_epub(path, "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)
    old_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path),)
    ).fetchone()[0]

    make_epub(path, "Dune", "Frank Herbert")
    _bump_bytes(path)

    scan(cfg, db, store, cfg.library.incoming, NOOP)

    new_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path),)
    ).fetchone()[0]
    assert new_sha != old_sha
    sidecar_path = path.with_name(path.name + ".json")
    assert sidecar_path.exists()
    book = store.load(new_sha, str(path))
    assert book is not None
    assert book.sha256 == new_sha


def test_carry_preserves_a_duplicates_sidecar_and_index_row(env):
    """Two paths sharing a hash: changing one must not delete the shared
    sidecar or index row the other path still legitimately refers to."""
    cfg, db, store = env
    path_a = cfg.library.incoming / "a.epub"
    path_b = cfg.library.incoming / "b.epub"
    make_epub(path_a, "Dune", "Frank Herbert")
    make_epub(path_b, "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)

    shared_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path_a),)
    ).fetchone()[0]
    assert (
        db.conn.execute(
            "SELECT sha256 FROM files WHERE path = ?", (str(path_b),)
        ).fetchone()[0]
        == shared_sha
    )

    make_epub(path_a, "Dune", "Frank Herbert")
    _bump_bytes(path_a)

    scan(cfg, db, store, cfg.library.incoming, NOOP)

    new_sha_a = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path_a),)
    ).fetchone()[0]
    assert new_sha_a != shared_sha
    assert store.load(shared_sha, str(path_b)) is not None

    from reshelf.store import index

    rows, _total = index.query(db.conn, q="Dune")
    assert any(r["sha256"] == shared_sha for r in rows)
