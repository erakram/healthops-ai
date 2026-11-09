"""OCR for scanned (image-only) PDF pages, on the machine itself.

On macOS it uses Apple's Vision framework through a tiny helper compiled from
vision_ocr.swift on first use (no packages to install; the binary is about 100 KB and lives
in data/bin/). Words come back with their positions, so evidence highlighting works on scans
like on text PDFs. Elsewhere (Linux, Docker) `available()` is False and scanned pages are
flagged "needs OCR" for a person instead of being read as empty.
"""

import json
import logging
import platform
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from app.config import BACKEND_ROOT

logger = logging.getLogger(__name__)
SOURCE = Path(__file__).resolve().parent / "vision_ocr.swift"


def _binary() -> Path:
    return BACKEND_ROOT / "data" / "bin" / "vision-ocr"  # one copy, outside git (data/ is ignored)


@lru_cache
def available() -> bool:
    if platform.system() != "Darwin":
        return False
    binary = _binary()
    if binary.exists() and binary.stat().st_mtime >= SOURCE.stat().st_mtime:
        return True
    if not shutil.which("swiftc"):
        return False
    binary.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["swiftc", "-O", str(SOURCE), "-o", str(binary)], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.warning("Couldn't build the OCR helper: %s", result.stderr[-500:])
        return False
    return True


def ocr_png(png: bytes) -> list[dict]:
    """Words on the image: [{text, x0, y0, x1, y1}] as fractions of the page, top-left origin."""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        f.write(png)
        f.flush()
        result = subprocess.run([str(_binary()), f.name], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"OCR failed: {result.stderr[-300:]}")
    return json.loads(result.stdout or "[]")
