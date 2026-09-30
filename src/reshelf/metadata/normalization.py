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


# Embedded authors that are the machine's account name or the producing tool.
_JUNK_AUTHOR = re.compile(
    r"(?i)^(administrator|admin|user|owner|unknown|微软用户|雨林木风|chatgpt\b.*|ms user|wps office|office|ssreader|cnki)$"
)


def is_junk_author(author: str | None) -> bool:
    return not author or bool(_JUNK_AUTHOR.search(author.strip()))


_CN_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")}
_VOLUME = re.compile(
    r"[\s(（]*(?:"
    r"第?\s*(?P<d>\d{1,3})\s*[卷册部集辑]?"  # "文集 02", "全编007", "第3卷"
    r"|第\s*(?P<c>[一二三四五六七八九十]{1,3})\s*[卷册部集辑]"  # "第三卷"
    r"|卷\s*(?P<k>[一二三四五六七八九十]{1,3})"  # "卷二"
    r"|(?P<h>[上中下])\s*[卷册]"  # "上册"
    r"|(?<=[(（])(?P<p>[上中下])(?=[)）])"  # "(上)"
    r")\s*[)）]?\s*$"
)


def _cn_int(s: str) -> int:
    if "十" not in s:
        return int("".join(str(_CN_DIGITS[c]) for c in s))
    tens, _, ones = s.partition("十")
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


def _has_words(s: str) -> bool:
    """At least two letters or one CJK character: "2023" is not a title."""
    return bool(re.search(r"[一-鿿]", s)) or len(re.findall(r"[^\W\d_]", s)) >= 2


def split_volume(title: str | None) -> tuple[str, str | None]:
    """("胡适文集", "2") from "胡适文集 02" / "胡适文集(2)" / "胡适文集第二卷".

    None when there is no volume marker, or nothing would be left of the
    title ("1984" is a title, not volume 1984 of nothing).
    """
    title = title or ""
    m = _VOLUME.search(title)
    base = title[: m.start()].strip(" -_.·") if m else ""
    if not m or not _has_words(base):
        return title, None
    d, c, k, h, p = m.group("d", "c", "k", "h", "p")
    vol = str(int(d)) if d else str(_cn_int(c or k)) if (c or k) else (h or p)
    return base, vol


def drop_subtitle(title: str) -> str:
    """Main title before a dash-style or (for Chinese) space-separated subtitle."""
    head = re.split(r"——|--|\+\+| - ", title, maxsplit=1)[0].strip()
    if re.search(r"[一-鿿]", head):
        head = head.split()[0] if head.split() else head
    return head if _has_words(head) else title


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
