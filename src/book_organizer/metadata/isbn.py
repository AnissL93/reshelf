import re

_PREFIX = re.compile(r"(?i)isbn(?:-1[03])?:?\s*")
_CANDIDATE = re.compile(r"[0-9][0-9Xx\- ]{8,16}[0-9Xx]")


def _clean(raw: str) -> str:
    return re.sub(r"[\s\-]", "", _PREFIX.sub("", raw)).upper()


def is_valid_isbn10(s: str) -> bool:
    if not re.fullmatch(r"[0-9]{9}[0-9X]", s):
        return False
    total = sum(
        (10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)
    )
    return total % 11 == 0


def is_valid_isbn13(s: str) -> bool:
    if not re.fullmatch(r"[0-9]{13}", s):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
    return total % 10 == 0


def isbn10_to_isbn13(s: str) -> str:
    core = "978" + s[:9]
    check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core)) % 10) % 10
    return core + str(check)


def normalize_isbn(raw: str) -> str | None:
    s = _clean(raw)
    if is_valid_isbn13(s):
        return s
    if is_valid_isbn10(s):
        return isbn10_to_isbn13(s)
    return None


def find_isbns(text: str | None) -> list[str]:
    out: list[str] = []
    for m in _CANDIDATE.finditer(text or ""):
        n = normalize_isbn(m.group(0))
        if n and n not in out:
            out.append(n)
    return out
