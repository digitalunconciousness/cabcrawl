"""ROM images from GATBOX: the resumable upload and the stored files (contract v1, "Files",
capability roms).

A file is named by the SHA-256 of its bytes and kept in ``ROM_FILES_DIR/<aa>/<sha256>.rom``,
which is inside ``uploads/`` (writable under the service's sandbox, and already in the uploads
backup) but a folder deep: the one route that serves files from ``uploads/`` takes a bare
name, so nothing here can be reached by URL. An upload in progress is a folder of numbered
parts in ``.incoming/<sha256>/`` with its announced size; nothing in it is a file until a
finish has joined the parts and checked the size and the hash. Half-sent uploads older than a
week are removed the next time anyone begins one.

Every call is safe to repeat: begin on a stored file says so, a part sent twice replaces
itself with the same bytes, finish on a stored file says so. It raises its own FileProblem,
which the routes turn into the contract's errors.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import zlib
from pathlib import Path

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import RomBurn, RomDump, RomFile

PART_SIZE = 8 * 1024 * 1024                    # contract v1: every part but the last
MAX_FILE = 32 * 1024 * 1024                    # contract v1: a bigger image stays on the Pi
STALE_AFTER = 7 * 24 * 3600
HEX = "0123456789abcdef"


class FileProblem(Exception):
    """A refused files call; ``code`` is the HTTP status the contract gives it."""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def parts_for(size: int) -> int:
    return -(-size // PART_SIZE)


def check_hash(value) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in HEX for c in value):
        raise FileProblem("sha256 must be 64 lowercase hex characters")
    return value


def root() -> Path:
    path = Path(current_app.config["ROM_FILES_DIR"])
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    return path


def path_for(sha256: str) -> Path:
    return root() / sha256[:2] / f"{sha256}.rom"


def _incoming(sha256: str) -> Path:
    return root() / ".incoming" / sha256


def _meta(sha256: str):
    try:
        return json.loads((_incoming(sha256) / "size.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _have(sha256: str, parts: int) -> list[int]:
    return [n for n in range(parts) if (_incoming(sha256) / f"{n}.part").is_file()]


def stored(sha256: str):
    """The RomFile row for these bytes, when the bytes are really on disk."""
    row = RomFile.query.filter_by(sha256=sha256).first()
    return row if row is not None and path_for(sha256).is_file() else None


def _answer_stored(row) -> dict:
    return {"state": "stored", "sha256": row.sha256, "size": row.size_bytes}


def _sweep() -> None:
    """Half-sent uploads nobody has touched for a week."""
    base = root() / ".incoming"
    if not base.is_dir():
        return
    cutoff = time.time() - STALE_AFTER
    for entry in base.iterdir():
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def begin(body) -> dict:
    if not isinstance(body, dict):
        raise FileProblem("body must be a JSON object")
    sha256 = check_hash(body.get("sha256"))
    size = body.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise FileProblem("size must be a whole number of bytes, 1 or more")
    if size > MAX_FILE:
        raise FileProblem(f"a file is at most {MAX_FILE} bytes (32 MiB), got {size}", 413)
    row = stored(sha256)
    if row is not None:
        return _answer_stored(row)
    _sweep()
    folder = _incoming(sha256)
    meta = _meta(sha256)
    if meta is not None and meta.get("size") != size:
        shutil.rmtree(folder, ignore_errors=True)            # a different announcement: start over
    folder.mkdir(mode=0o750, parents=True, exist_ok=True)
    (folder / "size.json").write_text(json.dumps({"size": size}), encoding="utf-8")
    os.utime(folder)
    parts = parts_for(size)
    return {"state": "open", "sha256": sha256, "size": size, "part_size": PART_SIZE,
            "parts": parts, "have": _have(sha256, parts)}


def put_part(sha256: str, n: int, data: bytes) -> dict:
    check_hash(sha256)
    row = stored(sha256)
    if row is not None:
        return _answer_stored(row)
    meta = _meta(sha256)
    if meta is None:
        raise FileProblem("begin the file first (POST /api/v1/files)", 404)
    size = meta["size"]
    parts = parts_for(size)
    if not 0 <= n < parts:
        raise FileProblem(f"part {n} is outside 0..{parts - 1}")
    want = PART_SIZE if n < parts - 1 else size - PART_SIZE * (parts - 1)
    if len(data) != want:
        raise FileProblem(f"part {n} must be exactly {want} bytes, got {len(data)}")
    folder = _incoming(sha256)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, folder / f"{n}.part")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.utime(folder)
    return {"state": "open", "sha256": sha256, "part": n, "have": _have(sha256, parts)}


def finish(sha256: str, device) -> dict:
    check_hash(sha256)
    row = stored(sha256)
    if row is not None:
        return _answer_stored(row)
    meta = _meta(sha256)
    if meta is None:
        raise FileProblem("begin the file first (POST /api/v1/files)", 404)
    size = meta["size"]
    parts = parts_for(size)
    have = _have(sha256, parts)
    if len(have) != parts:
        missing = [n for n in range(parts) if n not in have]
        raise FileProblem(f"parts missing: {missing}", 409)
    final = path_for(sha256)
    final.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=final.parent, prefix=".tmp-")
    digest, total = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as out:
            for n in range(parts):
                with open(_incoming(sha256) / f"{n}.part", "rb") as part:
                    while chunk := part.read(1 << 20):
                        digest.update(chunk)
                        total += len(chunk)
                        out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if total != size or digest.hexdigest() != sha256:
            shutil.rmtree(_incoming(sha256), ignore_errors=True)
            raise FileProblem("the parts don't join to the announced size and SHA-256: "
                              "the upload was dropped, send the file again", 422)
        os.replace(tmp, final)
    finally:
        Path(tmp).unlink(missing_ok=True)
    try:
        with db.session.begin_nested():
            db.session.add(RomFile(sha256=sha256, size_bytes=size, device_id=device.id))
    except IntegrityError:
        pass                                                  # another request stored it first
    shutil.rmtree(_incoming(sha256), ignore_errors=True)
    return _answer_stored(RomFile.query.filter_by(sha256=sha256).one())


def named_by_a_record(sha256: str, device):
    """The stored file, when a dump or a burn of a machine this device may use names it: the
    only files a device may read back."""
    row = RomFile.query.filter_by(sha256=sha256).first()
    if row is None:
        return None
    for model in (RomDump, RomBurn):
        for record in model.query.filter_by(rom_file_id=row.id):
            if device.allows(record.game):
                return row
    return None


def digests(sha256: str) -> tuple[str, str]:
    """(sha1, crc32 as 8 hex digits) of a stored file, read from disk."""
    sha1, crc = hashlib.sha1(), 0
    with open(path_for(sha256), "rb") as f:
        while chunk := f.read(1 << 20):
            sha1.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return sha1.hexdigest(), format(crc & 0xFFFFFFFF, "08x")
