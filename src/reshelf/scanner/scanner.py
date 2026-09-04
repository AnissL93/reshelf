from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class FileInfo:
    path: str
    size: int
    mtime: int
    extension: str


def iter_files(
    root: Path, formats: list[str], recursive: bool = True
) -> Iterator[FileInfo]:
    root = Path(root)
    paths = root.rglob("*") if recursive else root.glob("*")
    fmts = {f.lower() for f in formats}
    for p in sorted(paths):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        ext = p.suffix.lower().lstrip(".")
        if p.is_file() and ext in fmts:
            st = p.stat()
            yield FileInfo(str(p), st.st_size, int(st.st_mtime), ext)
