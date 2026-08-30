import re
import unicodedata

_MARKERS = re.compile(
    r"(?i)\b(\d+(?:st|nd|rd|th)\s+edition|revised edition|edition|ebook|epub|pdf|mobi|azw3?|retail|scan(?:ned)?)\b"
)
_ARTICLES = ("the ", "a ", "an ")

_LANG_MAP = {
    "eng": "en", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko",
    "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es",
    "ita": "it", "rus": "ru", "por": "pt",
}


def normalize_title(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    if ":" in s:
        head = s.split(":", 1)[0]
        if len(head.split()) >= 2:
            s = head
    s = _MARKERS.sub(" ", s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"[_\s]+", " ", s).strip()
    for art in _ARTICLES:
        if s.startswith(art):
            s = s[len(art):]
            break
    return s


def normalize_author(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def normalize_language(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip().lower().replace("_", "-").split("-")[0]
    if len(c) == 2:
        return c
    return _LANG_MAP.get(c, c[:2] if c else None)
