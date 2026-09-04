import hashlib

from reshelf.scanner.hashing import sha256_file
from reshelf.scanner.scanner import iter_files


def _touch(p, content=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_iter_files_filters_and_recurses(tmp_path):
    _touch(tmp_path / "a.epub")
    _touch(tmp_path / "B.PDF")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / ".hidden.epub")
    _touch(tmp_path / ".git" / "c.epub")
    _touch(tmp_path / "sub" / "c.epub")
    found = [f.path for f in iter_files(tmp_path, ["epub", "pdf"])]
    assert found == sorted(
        [str(tmp_path / "B.PDF"), str(tmp_path / "a.epub"), str(tmp_path / "sub" / "c.epub")]
    )
    flat = [f.path for f in iter_files(tmp_path, ["epub", "pdf"], recursive=False)]
    assert str(tmp_path / "sub" / "c.epub") not in flat


def test_fileinfo_fields(tmp_path):
    _touch(tmp_path / "a.epub", b"hello")
    fi = next(iter_files(tmp_path, ["epub"]))
    assert fi.size == 5 and fi.extension == "epub" and fi.mtime > 0


def test_sha256_file(tmp_path):
    _touch(tmp_path / "a.epub", b"hello")
    assert sha256_file(tmp_path / "a.epub") == hashlib.sha256(b"hello").hexdigest()
