from book_organizer.config import MatchingConfig
from book_organizer.matching.scorer import (
    LocalBook,
    band,
    confidence_from_score,
    score_candidate,
)
from book_organizer.metadata.models import Author, Candidate, Edition, Work


def _cand(title="The Three-Body Problem", author="Liu Cixin", isbn13=None,
          language=None, publisher=None, date=None):
    return Candidate(
        provider="openlibrary",
        provider_id="x",
        edition=Edition(
            work=Work(title=title, authors=[Author(name=author)]),
            isbn13=isbn13,
            language=language,
            publisher=publisher,
            publication_date=date,
        ),
    )


LOCAL = LocalBook(
    title="The Three-Body Problem",
    authors=["Liu Cixin"],
    isbn13s=["9780765382030"],
    language="en",
    year="2014",
)


def test_exact_isbn_gives_099():
    score, ev = score_candidate(LOCAL, _cand(isbn13="9780765382030", language="en", date="2014"))
    assert "exact_isbn" in ev and "exact_title" in ev
    assert confidence_from_score(score, ev) == 0.99


def test_isbn_conflict_caps_confidence():
    score, ev = score_candidate(LOCAL, _cand(isbn13="9780306406157", language="en", date="2014"))
    assert "conflict:isbn" in ev
    assert confidence_from_score(score, ev) <= 0.40


def test_title_author_match_without_isbn():
    local = LocalBook(title="Three-Body Problem", authors=["Liu Cixin"],
                      isbn13s=[], language=None)
    score, ev = score_candidate(local, _cand())
    assert "exact_author" in ev
    assert score >= 65  # exact/near-exact title + exact author


def test_author_conflict_penalized():
    score_same, _ = score_candidate(LOCAL, _cand(author="Liu Cixin"))
    score_diff, ev = score_candidate(LOCAL, _cand(author="Stephen King"))
    assert "conflict:author" in ev and score_diff < score_same


def test_bands():
    m = MatchingConfig()
    assert band(0.99, m) == "AUTO_ACCEPT"
    assert band(0.95, m) == "HIGH_CONFIDENCE"
    assert band(0.80, m) == "REVIEW_RECOMMENDED"
    assert band(0.60, m) == "AI_RESOLUTION"
    assert band(0.30, m) == "UNRESOLVED"


def test_confidence_clamped():
    assert confidence_from_score(-50, []) == 0.0
    assert confidence_from_score(500, []) == 1.0


def test_marketing_subtitle_still_exact_title():
    local = LocalBook(
        title="超新星纪元（刘慈欣的创作从《超新星纪元》开始！20万字未删节版！）",
        authors=["刘慈欣"],
    )
    score, ev = score_candidate(local, _cand(title="超新星纪元", author="刘慈欣"))
    assert "exact_title" in ev and "exact_author" in ev


def test_same_work():
    from book_organizer.matching.scorer import same_work

    a = _cand(title="超新星纪元", author="刘慈欣")
    b = _cand(title="超新星纪元（新版）", author="刘慈欣")
    c = _cand(title="三体Ⅱ", author="刘慈欣")
    assert same_work(a, b)
    assert not same_work(a, c)


def test_exact_title_and_author_floor():
    # work-level identity: exact title + exact author with no conflicts
    # must reach HIGH_CONFIDENCE even without ISBN/language/year evidence
    assert confidence_from_score(70, ["exact_title", "exact_author"]) >= 0.90
    assert (
        confidence_from_score(55, ["exact_title", "exact_author", "conflict:language"])
        < 0.90
    )
