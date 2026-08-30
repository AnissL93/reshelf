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


def clean_text(s: str | None) -> str | None:
    """Strip control characters and collapse whitespace; None if nothing left."""
    if s is None:
        return None
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def title_from_filename(stem: str) -> str:
    """Best-effort title from a filename stem (Z-Library / Anna's Archive junk)."""
    s = stem.split(" -- ")[0]
    s = re.sub(r"\([^()]*\)", " ", s)  # drop parenthesized authors/site tags
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    return s or stem


def short_title(s: str) -> str:
    """Main title for provider search: cut subtitles and bracketed suffixes."""
    head = re.split(r"[:：(（【\[]", s, maxsplit=1)[0].strip(" -_.")
    return head or s


def search_author(s: str | None) -> str | None:
    """First author, stripped of bracketed tags and 著/译/编 suffixes, for search."""
    if not s:
        return None
    s = re.sub(r"[（(【\[].*?[）)】\]]", " ", s)
    s = re.split(r"[;；,，/]", s)[0]
    s = re.sub(r"\s*(著|译|编著|编|主编|等)\s*$", "", s.strip())
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


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
