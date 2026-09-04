import json

from reshelf.metadata.models import Candidate

INSTRUCTIONS = """\
You are a book-metadata resolver. Decide which candidate record (if any)
describes the SAME BOOK as the local ebook below.

Rules:
- Titles and authors may be in different languages: a Chinese local title
  can match an English candidate (and vice versa) when they are the same
  work in translation. Author names may be transliterated (刘慈欣 = Liu
  Cixin) or carry annotations like (美), [英], 著, 译.
- Ignore marketing subtitles, edition markers, and bundle blurbs.
- A different work by the same author is NOT a match.
- Choose ONLY from the numbered candidates. Never invent ISBNs,
  publishers, or dates. If no candidate is the same work, decision is null.

Respond with ONLY a JSON object, no other text:
{"decision": <candidate index as integer, or null>,
 "confidence": <0.0-1.0>,
 "reasons": ["short reason", ...],
 "uncertainties": ["anything doubtful", ...]}
"""


def _candidate_payload(i: int, c: Candidate) -> dict:
    e = c.edition
    return {
        "index": i,
        "provider": c.provider,
        "title": e.work.title,
        "authors": [a.name for a in e.work.authors],
        "isbn13": e.isbn13,
        "publisher": e.publisher,
        "publication_date": e.publication_date,
        "language": e.language,
    }


def build_prompt(local: dict, candidates: list[Candidate]) -> str:
    payload = {
        "local_book": local,
        "candidates": [_candidate_payload(i, c) for i, c in enumerate(candidates)],
    }
    return INSTRUCTIONS + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)
