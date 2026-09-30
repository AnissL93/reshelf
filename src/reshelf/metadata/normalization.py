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


_SITE_TAG = re.compile(r"(?i)z-?lib|1lib|libgen|anna.?s.archive")


def parse_filename(stem: str) -> tuple[str, str | None]:
    """Best-effort (title, author) from a filename stem.

    Handles Anna's Archive `Title -- Author -- ...`, libgen
    `Author - Title (series) (year, pub) - libgen.li`, Z-Library
    `Title (Author) (Z-Library)` and `Title[Author][2011]`.
    """
    author = None
    if " -- " in stem:
        parts = stem.split(" -- ")
        s, author = parts[0], parts[1]
    else:
        s = stem
        m = re.match(r"(.+?) - (.+?)(?: - libgen[\w.]*)?$", s)
        if m and _SITE_TAG.search(s):
            author, s = m.group(1), m.group(2)
        else:
            groups = [g for g in re.findall(r"\(([^()]*)\)", s) if not _SITE_TAG.search(g)]
            author = groups[0] if groups else None
    tail = re.search(r"(\[[^\[\]]*\])+\s*$", s)  # trailing [author][year]
    if tail:
        if author is None:
            names = [g for g in re.findall(r"\[([^\[\]]+)\]", tail.group()) if not re.fullmatch(r"[\d\s.-]+", g)]
            author = names[0] if names else None
        s = s[: tail.start()]
    s = re.sub(r"^\s*(\[[^\]]*\]|【[^】]*】)\s*([A-Z]\d+\s)?", "", s)  # leading [series]A0406
    s = re.sub(r"\([^()]*\)", " ", s)  # drop parenthesized authors/site tags
    s = re.sub(r"^\d{1,3}\s*[-.、_]\s*", "", s.strip())  # leading "57-" numbering
    s = re.sub(r"-\d$", "", s.strip())  # "-1" duplicate-copy suffix
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    author = re.sub(r"\s+", " ", author).strip() if author else None
    return s or stem, author or None


def title_from_filename(stem: str) -> str:
    return parse_filename(stem)[0]


# Embedded PDF titles that are the producing tool, not the book.
_JUNK_TITLE = re.compile(
    r"(?i)^(ssreader|s?crack by|print$|untitled|unknown|microsoft word|outfile"
    r"|helloworld|ps22pdf|wps office|acdsee|km_c\d|book_\d|isbn_\d|[\w!]*\.(pdg|pdf|docx?|indd|s\d+)$"
    r"|<[0-9a-f]+>?$|[\da-f]{16,}$|\d+$|.$)"
)


def is_junk_title(title: str | None) -> bool:
    return not title or bool(_JUNK_TITLE.search(title.strip()))


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
