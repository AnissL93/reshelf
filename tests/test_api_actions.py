import time

import pytest
from fastapi.testclient import TestClient

from reshelf import pipeline
from reshelf.metadata.models import Author, Candidate, Edition, Work
from reshelf.web.app import create_app
from tests.test_api_metadata import SHA, build


class FakeProvider:
    """Matches the real MetadataProvider interface (lookup_isbn/search/enrich)."""

    name = "fake"

    def lookup_isbn(self, isbn):
        return []

    def search(self, title, author=None, language=None):
        return []

    def enrich(self, cand):
        return cand


class TwoCandidateProvider:
    """A provider whose `search` returns two distinct, scorable candidates -
    used to prove the rematch -> candidates -> choose round trip end to end.
    """

    name = "fake"

    def lookup_isbn(self, isbn):
        return []

    def search(self, title, author=None, language=None):
        return [
            Candidate(
                provider="fake",
                provider_id="fake-1",
                edition=Edition(
                    work=Work(title="Old Title", authors=[Author(name="Old Author")]),
                ),
            ),
            Candidate(
                provider="fake",
                provider_id="fake-2",
                edition=Edition(
                    work=Work(title="Unrelated Book", authors=[Author(name="Someone Else")]),
                ),
            ),
        ]

    def enrich(self, cand):
        return cand


def wait(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_convert_enqueues_a_job_and_produces_a_derived_file(tmp_path):
    cfg, path = build(tmp_path, fmt="txt")
    path.write_text("hello\n\nworld\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(f"/api/books/{SHA}/convert")
        assert r.status_code == 202
        job = wait(c, r.json()["job_id"])
        assert job["status"] == "done"
        detail = c.get(f"/api/books/{SHA}").json()
        derived = [f for f in detail["sidecar"]["files"] if f["role"] == "converted"]
        assert len(derived) == 1
        assert derived[0]["format"] == "epub"


def test_convert_on_an_already_usable_format_is_409(tmp_path):
    build(tmp_path, fmt="epub")
    with TestClient(create_app(tmp_path)) as c:
        assert c.post(f"/api/books/{SHA}/convert").status_code == 409


def test_convert_on_a_converted_book_still_checks_the_original_format(tmp_path):
    """A book with a converted EPUB primary must still be checked against its
    ORIGINAL (txt) format, not the EPUB - matching convert_book's own source
    selection. Converting again should succeed (409 would be the wrong error).
    """
    cfg, path = build(tmp_path, fmt="txt")
    path.write_text("hello\n\nworld\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as c:
        job = wait(c, c.post(f"/api/books/{SHA}/convert").json()["job_id"])
        assert job["status"] == "done"
        # Converting again is still valid: the original is still txt.
        r = c.post(f"/api/books/{SHA}/convert")
        assert r.status_code == 202


def test_convert_on_an_unknown_book_is_404(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        assert c.post("/api/books/" + "f" * 64 + "/convert").status_code == 404


def test_rematch_with_ai_is_refused_when_ai_is_off(tmp_path, monkeypatch):
    build(tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "build_providers", lambda cfg, client: calls.append(1) or [])
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(f"/api/books/{SHA}/rematch", json={"ai": True})
        assert r.status_code == 409
        assert "ai.provider" in r.json()["detail"]
    # The refusal must happen before any job runs - no provider (and so no
    # model, since match_one only calls the AI resolver after gathering
    # provider candidates) was ever reached.
    assert calls == []


def test_rematch_without_ai_enqueues_a_job(tmp_path, monkeypatch):
    build(tmp_path)
    # match_one builds real HTTP providers; stub them so the job the runner
    # actually executes in its background thread never opens a socket,
    # regardless of whether this test waits for it to finish.
    monkeypatch.setattr(pipeline, "build_providers", lambda cfg, client: [FakeProvider()])
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(f"/api/books/{SHA}/rematch", json={})
        assert r.status_code == 202
        assert "job_id" in r.json()
        job = wait(c, r.json()["job_id"])
        assert job["status"] == "done"


def test_rematch_populates_candidates_that_choose_can_then_pick(tmp_path, monkeypatch):
    """The regression guard for the whole Fix-metadata flow: `match_one`
    (run by the web "rematch" action) must leave something in `matches` for
    `/candidates` to return, or "Search providers" finds results the user
    has no way to click. See pipeline.match_one's `record_match` loop.
    """
    build(tmp_path)
    monkeypatch.setattr(pipeline, "build_providers", lambda cfg, client: [TwoCandidateProvider()])
    with TestClient(create_app(tmp_path)) as c:
        job = wait(c, c.post(f"/api/books/{SHA}/rematch", json={}).json()["job_id"])
        assert job["status"] == "done"

        candidates = c.get(f"/api/books/{SHA}/candidates").json()
        assert len(candidates) == 2
        by_title = {cand["title"]: cand for cand in candidates}
        assert set(by_title) == {"Old Title", "Unrelated Book"}
        # Exact title+author match must outscore the unrelated one - proves
        # the real score/confidence computed in match_one made it through to
        # the recorded row, not a placeholder.
        assert by_title["Old Title"]["score"] > by_title["Unrelated Book"]["score"]
        assert all(cand["resolver"] == "rematch" for cand in candidates)

        chosen = by_title["Old Title"]
        body = c.post(
            f"/api/books/{SHA}/choose", json={"candidate_id": chosen["edition_id"]}
        ).json()
        assert body["sidecar"]["metadata"]["title"] == "Old Title"
        assert body["sidecar"]["metadata"]["authors"] == ["Old Author"]
        assert body["sidecar"]["source"]["resolver"] == "human"


def test_rematch_replaces_its_own_prior_candidates_rather_than_piling_up(tmp_path, monkeypatch):
    """Clicking "Search providers" twice must not double the picker's rows -
    only this book's own earlier `resolver="rematch"` rows are cleared."""
    build(tmp_path)
    monkeypatch.setattr(pipeline, "build_providers", lambda cfg, client: [TwoCandidateProvider()])
    with TestClient(create_app(tmp_path)) as c:
        wait(c, c.post(f"/api/books/{SHA}/rematch", json={}).json()["job_id"])
        wait(c, c.post(f"/api/books/{SHA}/rematch", json={}).json()["job_id"])
        assert len(c.get(f"/api/books/{SHA}/candidates").json()) == 2


def test_rematch_does_not_delete_another_resolvers_matches(tmp_path, monkeypatch):
    """match_one's cleanup DELETE is scoped to `file_id AND resolver =
    'rematch'` - it must never touch a row the batch matcher (or a human's
    prior choice) already recorded for the same file. This is the
    provenance the library's 582 already-committed/matched books rely on;
    a "Search providers" click on one of them must not erase how it got
    there.
    """
    build(tmp_path)
    from reshelf.config import load_config
    from reshelf.db.database import Database

    cfg = load_config(tmp_path)
    db = Database(cfg.database.path)
    file_id = db.conn.execute("SELECT id FROM files WHERE sha256 = ?", (SHA,)).fetchone()["id"]
    batch_edition_id = db.save_candidate(
        Candidate(
            provider="openlibrary",
            provider_id="OL-batch",
            edition=Edition(
                work=Work(title="Batch Matched Title", authors=[Author(name="Batch Author")]),
            ),
        )
    )
    db.record_match(
        file_id, batch_edition_id, 95.0, 0.95, "deterministic", ["exact_isbn"], "AUTO_ACCEPT"
    )
    db.conn.commit()
    db.close()

    monkeypatch.setattr(pipeline, "build_providers", lambda cfg, client: [TwoCandidateProvider()])
    with TestClient(create_app(tmp_path)) as c:
        wait(c, c.post(f"/api/books/{SHA}/rematch", json={}).json()["job_id"])

        candidates = c.get(f"/api/books/{SHA}/candidates").json()
        # The pre-existing batch-matcher row plus the two fresh rematch rows.
        assert len(candidates) == 3
        assert {cand["resolver"] for cand in candidates} == {"deterministic", "rematch"}
        batch_row = next(cand for cand in candidates if cand["resolver"] == "deterministic")
        assert batch_row["title"] == "Batch Matched Title"
        assert batch_row["edition_id"] == batch_edition_id


def test_choose_makes_the_decision_human_and_sticky(tmp_path):
    from reshelf.config import load_config
    from reshelf.db.database import Database
    from reshelf.metadata.models import Author, Candidate, Edition, Work

    build(tmp_path)
    cfg = load_config(tmp_path)
    db = Database(cfg.database.path)
    edition_id = db.save_candidate(
        Candidate(
            provider="openlibrary",
            provider_id="OL1M",
            edition=Edition(
                work=Work(title="Chosen Title", authors=[Author(name="Chosen Author")]),
                isbn13="9780441013593",
            ),
        )
    )
    db.conn.commit()
    db.close()

    with TestClient(create_app(tmp_path)) as c:
        body = c.post(
            f"/api/books/{SHA}/choose", json={"candidate_id": edition_id}
        ).json()
        assert body["sidecar"]["metadata"]["title"] == "Chosen Title"
        assert body["sidecar"]["source"]["resolver"] == "human"


def test_choose_with_an_unknown_candidate_is_404(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        assert c.post(
            f"/api/books/{SHA}/choose", json={"candidate_id": 9999}
        ).status_code == 404
