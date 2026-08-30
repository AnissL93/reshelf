import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from book_organizer.config import MatchingConfig
from book_organizer.metadata.models import Candidate
from book_organizer.metadata.normalization import (
    normalize_author,
    normalize_title,
    search_author,
    short_title,
)


def _norm_title(s: str | None) -> str:
    return normalize_title(short_title(s) if s else s)


@dataclass
class LocalBook:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    isbn13s: list[str] = field(default_factory=list)
    language: str | None = None
    publisher: str | None = None
    year: str | None = None


def _author_variants(name: str) -> set[str]:
    out = set()
    for v in (name, search_author(name) or ""):
        nv = normalize_author(v)
        if nv:
            out.add(nv)
    return out


def _author_similarity(local_authors: list[str], cand_authors: list[str]) -> float:
    """Max similarity across annotation/translation variants of each name."""
    best = 0.0
    for x in local_authors:
        for y in cand_authors:
            for vx in _author_variants(x):
                for vy in _author_variants(y):
                    if vx == vy:
                        return 1.0
                    if len(vx) >= 2 and len(vy) >= 2 and (vx in vy or vy in vx):
                        best = max(best, 0.95)
                    best = max(best, fuzz.token_sort_ratio(vx, vy) / 100)
    return best


def _year(date: str | None) -> int | None:
    m = re.search(r"\d{4}", date or "")
    return int(m.group()) if m else None


def score_candidate(local: LocalBook, cand: Candidate) -> tuple[float, list[str]]:
    score = 0.0
    ev: list[str] = []
    e = cand.edition

    if e.isbn13 and local.isbn13s:
        if e.isbn13 in local.isbn13s:
            score += 100
            ev.append("exact_isbn")
        else:
            score -= 100
            ev.append("conflict:isbn")

    lt, ct = _norm_title(local.title), _norm_title(e.work.title)
    if lt and ct:
        if lt == ct:
            score += 40
            ev.append("exact_title")
        else:
            sim = fuzz.token_sort_ratio(lt, ct) / 100
            if sim >= 0.95:
                score += 35
                ev.append("title_sim>=0.95")
            elif sim >= 0.85:
                score += 25
                ev.append("title_sim>=0.85")

    la, ca = local.authors, [a.name for a in e.work.authors]
    if la and ca:
        best = _author_similarity(la, ca)
        if best == 1.0:
            score += 30
            ev.append("exact_author")
        elif best >= 0.90:
            score += 25
            ev.append("author_sim>=0.90")
        elif best < 0.50:
            score -= 40
            ev.append("conflict:author")

    if local.language and e.language:
        if local.language == e.language:
            score += 10
            ev.append("language_match")
        else:
            score -= 15
            ev.append("conflict:language")

    if local.publisher and e.publisher:
        if normalize_title(local.publisher) == normalize_title(e.publisher):
            score += 8
            ev.append("publisher_match")

    ly, cy = _year(local.year), _year(e.publication_date)
    if ly and cy:
        diff = abs(ly - cy)
        if diff == 0:
            score += 7
            ev.append("year_exact")
        elif diff <= 1:
            score += 5
            ev.append("year_close")
        elif diff > 20:
            score -= 10
            ev.append("conflict:year")

    return score, ev


def same_work(a: Candidate, b: Candidate) -> bool:
    """True when two candidates plausibly describe the same work."""
    if _norm_title(a.edition.work.title) != _norm_title(b.edition.work.title):
        return False
    aa = {normalize_author(x.name) for x in a.edition.work.authors}
    ba = {normalize_author(x.name) for x in b.edition.work.authors}
    return not aa or not ba or bool(aa & ba)


def confidence_from_score(score: float, evidence: list[str]) -> float:
    if "exact_isbn" in evidence and not any(e.startswith("conflict:") for e in evidence):
        return 0.99
    conf = max(0.0, min(score / 100, 1.0))
    if not any(e.startswith("conflict:") for e in evidence):
        if "exact_title" in evidence and "exact_author" in evidence:
            conf = max(conf, 0.90)  # work-level identity
        elif "exact_title" in evidence:
            conf = max(conf, 0.50)  # weak but worth human review
    if "conflict:isbn" in evidence:
        conf = min(conf, 0.40)
    return conf


def band(confidence: float, m: MatchingConfig) -> str:
    if confidence >= m.auto_accept:
        return "AUTO_ACCEPT"
    if confidence >= m.review_below:
        return "HIGH_CONFIDENCE"
    if confidence >= m.ai_resolve_below:
        return "REVIEW_RECOMMENDED"
    if confidence >= m.unresolved_below:
        return "AI_RESOLUTION"
    return "UNRESOLVED"
