import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from reshelf.extractors.base import ExtractedMetadata, ExtractionError
from reshelf.metadata.isbn import find_isbns
from reshelf.metadata.normalization import clean_text, normalize_language

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"


def extract_epub(path: Path) -> ExtractedMetadata:
    try:
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = container.find(f".//{_CONTAINER_NS}rootfile")
            if rootfile is None:
                raise ExtractionError("no rootfile in container.xml")
            opf = ET.fromstring(z.read(rootfile.attrib["full-path"]))
    except ExtractionError:
        raise
    except Exception as e:  # bad zips, malformed XML, missing entries, ...
        raise ExtractionError(str(e)) from e

    md = opf.find(f"{_OPF_NS}metadata")
    if md is None:
        raise ExtractionError("no metadata element in OPF")

    def dc(name: str) -> list[str]:
        out = []
        for el in md:
            if el.tag.endswith("}" + name):
                text = clean_text(el.text)
                if text:
                    out.append(text)
        return out

    isbns: list[str] = []
    for ident in dc("identifier") + dc("source"):
        for isbn in find_isbns(ident):
            if isbn not in isbns:
                isbns.append(isbn)

    titles, creators = dc("title"), dc("creator")
    langs, pubs = dc("language"), dc("publisher")
    dates, descs = dc("date"), dc("description")
    return ExtractedMetadata(
        title=titles[0] if titles else None,
        authors=creators,
        isbns=isbns,
        language=normalize_language(langs[0]) if langs else None,
        publisher=pubs[0] if pubs else None,
        date=dates[0] if dates else None,
        description=descs[0] if descs else None,
    )
