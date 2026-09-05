"""Format conversion via calibre's ebook-convert CLI."""

import subprocess
from pathlib import Path

KINDLE_FORMATS = {".mobi", ".azw", ".azw3"}


class ConversionError(Exception):
    pass


def convert_to_epub(src: Path, dest: Path, timeout: int = 600) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["ebook-convert", str(src), str(dest)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise ConversionError("ebook-convert not found (install calibre)") from e
    except subprocess.TimeoutExpired as e:
        raise ConversionError(f"conversion timed out after {timeout}s") from e
    if proc.returncode != 0 or not dest.exists():
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise ConversionError(tail[0][:200])
