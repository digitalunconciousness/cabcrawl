"""A read-only copy of the games this floor's machines name, from the GATBOX archive.

The archive owns the library (docs: contract/library-v1/). This application never edits it:
``scripts/pull_library.py`` replaces rows with what the archive sends, and a game a machine no longer
names keeps its last copy until a manager clears it. One row per library record, whatever its kind
(game, document, MAME set, ROM list, ROM image mark, rail limit, fact), with its data as the archive
sent it, so a field the archive adds later needs no migration here.

The documents' PDFs are not here: they are files in ``LIBRARY_FILES_DIR``, named by SHA-256, behind
the application's login (app/services/library_files.py).

Timestamps are naive UTC, like the rest of the rail data (see rail.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LibraryRecord(db.Model):
    """One record of the archive's, as last read: ``(kind, rid)`` names it, ``rev`` is its revision."""

    __tablename__ = "library_record"
    __table_args__ = (db.UniqueConstraint("kind", "rid", name="uq_library_record_kind_rid"),)

    id: int = db.Column(db.Integer, primary_key=True)
    kind: str = db.Column(db.String(16), nullable=False)
    rid: str = db.Column(db.String(200), nullable=False)
    # The game a record belongs to (a game is its own key). A ROM image may name none.
    model_key: str | None = db.Column(db.String(60), nullable=True, index=True)
    rev: int = db.Column(db.Integer, nullable=False)
    hash: str = db.Column(db.String(64), nullable=False)
    at: float | None = db.Column(db.Float, nullable=True)           # when the archive's last write was made
    by: str | None = db.Column(db.String(60), nullable=True)
    data = db.Column(db.JSON, nullable=False)
    fetched: datetime = db.Column(db.DateTime, default=_now, nullable=False)


class LibraryState(db.Model):
    """The pull job's own bookkeeping: one row, ``id`` 1. Where it has read to, and how the last run went."""

    __tablename__ = "library_state"

    id: int = db.Column(db.Integer, primary_key=True)
    cursor: int = db.Column(db.BigInteger, nullable=False, default=0)
    # The keys read from cursor 0 already: a key a machine gains later is read from the start, once.
    keys_synced = db.Column(db.JSON, nullable=False, default=list)
    last_try: datetime | None = db.Column(db.DateTime, nullable=True)
    last_ok: datetime | None = db.Column(db.DateTime, nullable=True)
    error: str | None = db.Column(db.Text, nullable=True)
    refused: bool = db.Column(db.Boolean, nullable=False, default=False)   # the archive said no to our token
    records: int = db.Column(db.Integer, nullable=False, default=0)
