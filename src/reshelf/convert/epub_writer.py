"""Build and rewrite EPUB 3 containers.

A zip cannot be edited in place, so rewriting rebuilds into a temp file
and os.replace()s it over the original. `mimetype` must be the first
entry and stored uncompressed or readers reject the file.
"""

import os
import re
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from reshelf.store.models import BookMetadata

MIMETYPE = "application/epub+zip"

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_MEDIA_TYPES = {
    ".xhtml": "application/xhtml+xml",
    ".html": "application/xhtml+xml",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>
<body>{body}</body></html>"""


class UnsupportedEpub(Exception):
    pass


def _media_type(name: str) -> str:
    return _MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _opf(title, authors, language, identifier, chapters, resources) -> str:
    items = []
    for i, (name, _) in enumerate(chapters):
        items.append(
            f'<item id="c{i}" href={quoteattr(name)}'
            f' media-type="application/xhtml+xml"/>'
        )
    for i, name in enumerate(sorted(resources or {})):
        items.append(
            f'<item id="r{i}" href={quoteattr(name)}'
            f' media-type={quoteattr(_media_type(name))}/>'
        )
    items.append(
        '<item id="nav" href="nav.xhtml"'
        ' media-type="application/xhtml+xml" properties="nav"/>'
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    creators = "".join(f"<dc:creator>{escape(a)}</dc:creator>" for a in authors)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{escape(identifier)}</dc:identifier>
    <dc:title>{escape(title or "Untitled")}</dc:title>
    {creators}
    <dc:language>{escape(language or "en")}</dc:language>
  </metadata>
  <manifest>{"".join(items)}</manifest>
  <spine>{spine}</spine>
</package>"""


def _write_zip(dest: Path, entries: list[tuple[str, bytes]]) -> Path:
    """mimetype first and stored; everything else deflated. Atomic."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED
            )
            for name, data in entries:
                if name == "mimetype":
                    continue
                z.writestr(name, data)
        os.replace(tmp, dest)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def write_epub(
    dest: Path,
    *,
    title: str,
    authors: list[str],
    language: str = "en",
    chapters: list[tuple[str, str]],
    identifier: str | None = None,
    resources: dict[str, bytes] | None = None,
) -> Path:
    identifier = identifier or f"urn:uuid:{uuid.uuid4()}"
    entries: list[tuple[str, bytes]] = [
        ("META-INF/container.xml", CONTAINER.encode()),
        (
            "content.opf",
            _opf(title, authors, language, identifier, chapters, resources).encode(),
        ),
        (
            "nav.xhtml",
            XHTML.format(
                title=escape(title or "Untitled"),
                body='<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">'
                "<ol>"
                + "".join(
                    f"<li><a href={quoteattr(n)}>{escape(n)}</a></li>"
                    for n, _ in chapters
                )
                + "</ol></nav>",
            ).encode(),
        ),
    ]
    for name, body in chapters:
        entries.append(
            (name, XHTML.format(title=escape(title or ""), body=body).encode())
        )
    for name, data in (resources or {}).items():
        entries.append((name, data))
    return _write_zip(dest, entries)


def find_opf(z: zipfile.ZipFile) -> str:
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", "replace")
    except KeyError:
        container = ""
    match = re.search(r'full-path="([^"]+)"', container)
    if match:
        return match.group(1)
    # Fallback: no (usable) container.xml. Only accept a candidate that is
    # actually a package document - a stray *.opf elsewhere in the zip must
    # not be mistaken for the real one.
    for name in z.namelist():
        if name.lower().endswith(".opf"):
            if "<package" in z.read(name).decode("utf-8", "replace"):
                return name
    raise UnsupportedEpub("no OPF found")


def _tag_pattern(tag: str) -> re.Pattern:
    """Match a dc:<tag> element, self-closing or with a body.

    The self-closing form must come first: tried second, `[^>]*>` would
    swallow a `/>` and then hunt for a `</dc:tag>` that may not belong to
    this element (or may not exist at all).
    """
    return re.compile(rf"<dc:{tag}\b[^>]*/>|<dc:{tag}\b[^>]*>.*?</dc:{tag}>", re.DOTALL)


def _replace_tag(opf: str, tag: str, value: str | None) -> str:
    pattern = _tag_pattern(tag)
    if value is None:
        return pattern.sub("", opf)
    replacement = f"<dc:{tag}>{escape(value)}</dc:{tag}>"
    if pattern.search(opf):
        return pattern.sub(replacement, opf, count=1)
    return re.sub(r"(<metadata[^>]*>)", rf"\1{replacement}", opf, count=1)


def rewrite_metadata(path: Path, metadata: BookMetadata) -> None:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            opf_name = find_opf(z)
            entries = [(n, z.read(n)) for n in z.namelist()]
            opf = z.read(opf_name).decode("utf-8", "replace")
    except zipfile.BadZipFile as e:
        raise UnsupportedEpub(str(e)) from e

    opf = _replace_tag(opf, "title", metadata.title)
    opf = _tag_pattern("creator").sub("", opf)
    creators = "".join(
        f"<dc:creator>{escape(a)}</dc:creator>" for a in metadata.authors
    )
    opf = re.sub(r"(<metadata[^>]*>)", rf"\1{creators}", opf, count=1)
    if metadata.language:
        opf = _replace_tag(opf, "language", metadata.language)
    if metadata.publisher:
        opf = _replace_tag(opf, "publisher", metadata.publisher)

    entries = [(n, opf.encode() if n == opf_name else d) for n, d in entries]
    _write_zip(path, entries)
