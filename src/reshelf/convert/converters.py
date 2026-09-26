"""Format conversion without Calibre.

MOBI/AZW3 go through the `mobi` package (a KindleUnpack fork): AZW3/KF8
comes out as an EPUB directly, older MOBI6 as HTML plus resources, which
we assemble with epub_writer. TXT is wrapped. DjVu is scanned page
images, so it targets PDF via ddjvu rather than a zip of JPEGs.
"""

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from reshelf.convert.epub_writer import write_epub

TARGETS = {
    "mobi": "epub",
    "azw": "epub",
    "azw3": "epub",
    "txt": "epub",
    "djvu": "pdf",
}

# Tried in order; a Chinese library is full of GBK text files.
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")
_RESOURCE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".webp")


class ConversionError(Exception):
    pass


def target_format(fmt: str) -> str | None:
    return TARGETS.get((fmt or "").lower().lstrip("."))


def available() -> dict[str, bool]:
    try:
        import mobi  # noqa: F401

        has_mobi = True
    except ImportError:
        has_mobi = False
    return {
        "mobi": has_mobi,
        "txt": True,
        "djvu": shutil.which("ddjvu") is not None,
    }


def _decode(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _txt_to_epub(src: Path, dest: Path) -> Path:
    text = _decode(src.read_bytes())
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs) or "<p></p>"
    return write_epub(
        dest,
        title=src.stem,
        authors=[],
        chapters=[("text.xhtml", body)],
    )


def _mobi_to_epub(src: Path, dest: Path) -> Path:
    try:
        import mobi
    except ImportError as e:
        raise ConversionError("the `mobi` package is not installed") from e
    # mobi.extract() mkdtemp()s *before* unpacking, so a mid-unpack failure
    # leaks that directory - it never reaches us as `tempdir`. Snapshot and
    # sweep any that appear during a failed call; belt-and-braces alongside
    # the `finally` below, which only covers the success path.
    tmp_root = Path(tempfile.gettempdir())
    before = set(tmp_root.glob("mobiex*"))
    try:
        tempdir, produced = mobi.extract(str(src))
    except Exception as e:  # KindleUnpack raises a zoo of exceptions
        for leaked in set(tmp_root.glob("mobiex*")) - before:
            shutil.rmtree(leaked, ignore_errors=True)
        raise ConversionError(f"could not unpack {src.name}: {e}") from e
    try:
        produced = Path(produced)
        if produced.suffix.lower() == ".epub":
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(produced, dest)
            return dest
        if produced.suffix.lower() in (".html", ".htm"):
            return _html_tree_to_epub(produced, dest, src.stem)
        raise ConversionError(f"unpacked to an unusable {produced.suffix} file")
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _html_tree_to_epub(html_file: Path, dest: Path, title: str) -> Path:
    """MOBI6 unpacks to one HTML file plus a sibling resource tree."""
    root = html_file.parent
    body = _decode(html_file.read_bytes())
    resources: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _RESOURCE_SUFFIXES:
            resources[path.relative_to(root).as_posix()] = path.read_bytes()
    return write_epub(
        dest,
        title=title,
        authors=[],
        chapters=[("text.xhtml", body)],
        resources=resources,
    )


def _djvu_to_pdf(src: Path, dest: Path, timeout: int) -> Path:
    if shutil.which("ddjvu") is None:
        raise ConversionError("ddjvu not found (install djvulibre-bin)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            ["ddjvu", "-format=pdf", "-quality=85", str(src), str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or tmp_path.stat().st_size == 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["failed"]
            raise ConversionError(tail[0][:200])
        shutil.move(str(tmp_path), dest)
        return dest
    except subprocess.TimeoutExpired as e:
        raise ConversionError(f"ddjvu timed out after {timeout}s") from e
    finally:
        tmp_path.unlink(missing_ok=True)


def convert(src: Path, dest: Path, timeout: int = 300) -> Path:
    src, dest = Path(src), Path(dest)
    fmt = src.suffix.lower().lstrip(".")
    target = target_format(fmt)
    if target is None:
        raise ConversionError(f"nothing to convert: {fmt or 'unknown'} is already usable")
    if fmt == "txt":
        return _txt_to_epub(src, dest)
    if fmt == "djvu":
        return _djvu_to_pdf(src, dest, timeout)
    return _mobi_to_epub(src, dest)
