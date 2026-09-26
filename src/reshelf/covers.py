"""Cover images for the library grid.

EPUB: the manifest item marked properties="cover-image", falling back to
the first image in the zip. PDF: the first page, rendered by pymupdf.
MOBI/AZW3 get nothing until they are converted (Task 11), because there
is no Calibre here to read them.
"""

import re
import zipfile
from pathlib import Path

import pymupdf as fitz

_COVER_ITEM = re.compile(
    r'<item[^>]*properties="[^"]*cover-image[^"]*"[^>]*href="([^"]+)"'
    r"|"
    r'<item[^>]*href="([^"]+)"[^>]*properties="[^"]*cover-image[^"]*"',
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def cover_path(covers_dir: Path, sha256: str) -> Path:
    return Path(covers_dir) / f"{sha256}.jpg"


def thumb_path(covers_dir: Path, sha256: str) -> Path:
    return Path(covers_dir) / f"{sha256}.thumb.jpg"


def _save(pix: fitz.Pixmap, dest: Path, max_edge: int) -> None:
    """Shrink to fit within max_edge (approximate power-of-two steps) and save.

    A JPEG can't hold an alpha channel, so that's dropped first.
    """
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    longest = max(pix.width, pix.height)
    shrink = 0
    while longest > max_edge and shrink < 4:
        shrink += 1
        longest //= 2
    if shrink:
        pix.shrink(shrink)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(dest))


def _from_pdf(src: Path, dest: Path, max_edge: int) -> bool:
    with fitz.open(str(src)) as doc:
        if doc.page_count == 0:
            return False
        page = doc.load_page(0)
        longest_edge = max(page.rect.width, page.rect.height)
        zoom = min(1.0, max_edge / longest_edge) if longest_edge else 1.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    _save(pix, dest, max_edge)
    return True


def _epub_cover_bytes(src: Path) -> bytes | None:
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        opfs = [n for n in names if n.lower().endswith(".opf")]
        for opf in opfs:
            match = _COVER_ITEM.search(z.read(opf).decode("utf-8", "replace"))
            if not match:
                continue
            href = match.group(1) or match.group(2)
            base = Path(opf).parent
            for candidate in (str(base / href), href):
                normalized = candidate.replace("\\", "/").lstrip("./")
                if normalized in names:
                    return z.read(normalized)
        images = [n for n in names if n.lower().endswith(_IMAGE_SUFFIXES)]
        if images:
            return z.read(sorted(images)[0])
    return None


def _from_epub(src: Path, dest: Path, max_edge: int) -> bool:
    data = _epub_cover_bytes(src)
    if not data:
        return False
    _save(fitz.Pixmap(data), dest, max_edge)
    return True


def extract_cover(src: Path, dest: Path, max_edge: int = 600) -> bool:
    """True if a cover was written. Never raises on a bad file."""
    src, dest = Path(src), Path(dest)
    suffix = src.suffix.lower()
    try:
        if suffix == ".pdf":
            return _from_pdf(src, dest, max_edge)
        if suffix == ".epub":
            return _from_epub(src, dest, max_edge)
    except Exception:
        return False
    return False


def ensure_cover(covers_dir: Path, sha256: str, src: Path) -> bool:
    """Extract full (600px) and thumbnail (200px) covers unless both exist."""
    full, thumb = cover_path(covers_dir, sha256), thumb_path(covers_dir, sha256)
    if full.exists() and thumb.exists():
        return True
    ok = extract_cover(src, full, max_edge=600)
    if not ok:
        return False
    return extract_cover(src, thumb, max_edge=200)
