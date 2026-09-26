import pytest

from reshelf.ai.resolver import AIDecision, ClaudeCLIResolver
from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.metadata.models import Author, Candidate, Edition, Work
from reshelf.pipeline import AIDisabledError, choose, match, match_one
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

NOOP = lambda *a: None  # noqa: E731


class FakeProvider:
    """Matches the real MetadataProvider interface (lookup_isbn/search/enrich)."""

    name = "fake"

    def __init__(self, candidates):
        self._candidates = list(candidates)

    def lookup_isbn(self, isbn):
        return []

    def search(self, title, author=None, language=None):
        return list(self._candidates)

    def enrich(self, cand):
        return cand


@pytest.fixture
def env(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    book = Book(
        sha256="a" * 64,
        files=[FileEntry(path="incoming/dune.epub", format="epub")],
    )
    book.metadata.title = "Dune"
    store.save(book)
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status, title_raw)"
        " VALUES ('incoming/dune.epub', ?, 'epub', 'IDENTIFIED', 'Dune')",
        ("a" * 64,),
    )
    db.conn.commit()
    yield cfg, db, store
    db.close()


def _dune_candidate(provider_id="1", title="Dune", isbn13=None):
    return Candidate(
        provider="fake",
        provider_id=provider_id,
        edition=Edition(
            work=Work(title=title, authors=[Author(name="Frank Herbert")]),
            isbn13=isbn13,
        ),
    )


def test_match_one_returns_ranked_candidates_without_deciding(env, monkeypatch):
    cfg, db, store = env
    cands = [_dune_candidate("1", "Dune"), _dune_candidate("2", "Dune Messiah")]
    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [FakeProvider(cands)]
    )
    result = match_one(cfg, db, store, "a" * 64)
    assert [c.edition.work.title for c in result] == ["Dune", "Dune Messiah"]
    assert all(c.edition_id is not None for c in result)
    assert store.load("a" * 64).source.resolver == "embedded"  # unchanged


def test_match_one_honours_a_query_override(env, monkeypatch):
    cfg, db, store = env
    seen = {}

    class Recorder(FakeProvider):
        def search(self, title, author=None, language=None):
            seen["title"] = title
            return []

    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [Recorder([])]
    )
    match_one(cfg, db, store, "a" * 64, query={"title": "Corrected Title"})
    assert seen["title"] == "Corrected Title"


def test_match_one_refuses_ai_when_no_provider_is_configured(env):
    cfg, db, store = env
    assert cfg.ai.provider is None
    with pytest.raises(AIDisabledError):
        match_one(cfg, db, store, "a" * 64, use_ai=True)


def test_match_one_ai_rank_reorders_by_ai_decision(env, monkeypatch):
    cfg, db, store = env
    cfg.ai.provider = "claude-cli"
    cands = [_dune_candidate("1", "Dune"), _dune_candidate("2", "Dune Messiah")]
    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [FakeProvider(cands)]
    )
    monkeypatch.setattr(
        ClaudeCLIResolver,
        "resolve",
        lambda self, local, candidates: AIDecision(decision=1, confidence=0.9),
    )
    result = match_one(cfg, db, store, "a" * 64, use_ai=True)
    assert result[0].edition.work.title == "Dune Messiah"


def test_choose_writes_a_sticky_human_decision(env):
    cfg, db, store = env
    cand = _dune_candidate("1", "Dune", isbn13="9780441013593")
    edition_id = db.save_candidate(cand)
    db.conn.commit()

    book = choose(cfg, db, store, "a" * 64, edition_id)
    assert book.metadata.title == "Dune"
    assert book.metadata.isbn13 == "9780441013593"
    assert book.source.resolver == "human"
    assert book.is_human

    from reshelf.store import index

    assert index.query(db.conn, q="Dune")[1] == 1


def test_choose_preserves_all_coauthors(env):
    cfg, db, store = env
    cand = Candidate(
        provider="fake",
        provider_id="1",
        edition=Edition(
            work=Work(
                title="Dune",
                authors=[Author(name="Frank Herbert"), Author(name="Brian Herbert")],
            ),
            isbn13="9780441013593",
        ),
    )
    edition_id = db.save_candidate(cand)
    db.conn.commit()

    book = choose(cfg, db, store, "a" * 64, edition_id)
    assert book.metadata.authors == ["Frank Herbert", "Brian Herbert"]


def test_match_skips_a_human_resolved_book(env, monkeypatch):
    cfg, db, store = env
    store.update("a" * 64, lambda b: setattr(b.source, "resolver", "human"))
    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [FakeProvider([])]
    )
    counts = match(cfg, db, store, offline=True, progress=NOOP)
    assert counts.get("SKIPPED_HUMAN") == 1


def test_match_writes_all_coauthors_to_sidecar(env, monkeypatch):
    cfg, db, store = env
    db.conn.execute(
        "UPDATE files SET isbn_raw = ? WHERE sha256 = ?",
        ("9780441013593", "a" * 64),
    )
    db.conn.commit()
    cand = Candidate(
        provider="fake",
        provider_id="1",
        edition=Edition(
            work=Work(
                title="Dune",
                authors=[Author(name="Frank Herbert"), Author(name="Brian Herbert")],
            ),
            isbn13="9780441013593",
        ),
    )
    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [FakeProvider([cand])]
    )
    counts = match(cfg, db, store, offline=True, progress=NOOP)
    assert counts.get("MATCHED") == 1
    book = store.load("a" * 64)
    assert book.metadata.authors == ["Frank Herbert", "Brian Herbert"]
    assert book.source.resolver == "deterministic"
