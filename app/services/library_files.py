"""The library's manuals: PDFs read from the archive, kept by SHA-256 (contract/library-v1/).

A file is ``LIBRARY_FILES_DIR/<aa>/<sha256>.pdf``. The folder is inside ``uploads/`` (writable under the
service's sandbox and already in the uploads backup) but a folder deep: the one route that serves from
``uploads/`` takes a bare name, so nothing here has a URL of its own. The pages open a manual through
``/library/file/<sha256>``, behind the application's login, and only for a hash a held document names.

A download in progress is ``.incoming/<sha256>.part``: what has arrived so far, so a cut-off one resumes from
the byte it reached. Nothing in it is a manual until the joined bytes have been checked against their name.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from flask import current_app

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def root() -> Path:
    path = Path(current_app.config["LIBRARY_FILES_DIR"])
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    return path


def valid(sha: str) -> bool:
    return isinstance(sha, str) and bool(HEX64.match(sha))


def path_for(sha: str) -> Path:
    return root() / sha[:2] / f"{sha}.pdf"


def held(sha: str) -> bool:
    return valid(sha) and path_for(sha).is_file()


def part_path(sha: str) -> Path:
    folder = root() / ".incoming"
    folder.mkdir(mode=0o750, exist_ok=True)
    return folder / f"{sha}.part"


def finish(sha: str) -> Path:
    """Move a complete download into place after checking its hash. Raises ValueError (and drops the part) on a mismatch."""
    part = part_path(sha)
    digest = hashlib.sha256()
    with open(part, "rb") as f:
        head = f.read(5)
        digest.update(head)
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != sha:
        part.unlink(missing_ok=True)
        raise ValueError("the downloaded bytes don't hash to their name: dropped, fetched again next run")
    if head != b"%PDF-":
        part.unlink(missing_ok=True)
        raise ValueError("a document that isn't a PDF: dropped")
    final = path_for(sha)
    final.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    part.replace(final)
    return final


def forget(sha: str) -> None:
    """Remove a manual no held document names any more (a manager cleared its game)."""
    if valid(sha):
        path_for(sha).unlink(missing_ok=True)
