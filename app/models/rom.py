"""ROM images from GATBOX: the files, the dumps and the burns (contract v1, capability roms).

The bytes of an image are in the file store (``ROM_FILES_DIR``), named by their SHA-256; here
are only the hash and the size. A dump and a burn are evidence the Pi computed and the hub has
checked against the stored image: this application adds them and never changes them.

Timestamps are naive UTC, like the rest of the rail data (see rail.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db

BURN_STATES = ("verified", "stopped")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RomFile(db.Model):
    """One distinct image, whoever sent it. A dump or a burn points at it."""

    __tablename__ = "rom_file"

    id: int = db.Column(db.Integer, primary_key=True)
    sha256: str = db.Column(db.String(64), unique=True, nullable=False)
    size_bytes: int = db.Column(db.Integer, nullable=False)
    device_id: int | None = db.Column(
        db.Integer, db.ForeignKey("device.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created: datetime = db.Column(db.DateTime, default=_now, nullable=False)


class RomDump(db.Model):
    """One chip read twice, identically, on the bench programmer (contract "rom_dump")."""

    __tablename__ = "rom_dump"

    id: int = db.Column(db.Integer, primary_key=True)
    uid: str = db.Column(db.String(32), unique=True, nullable=False)
    device_id: int = db.Column(
        db.Integer, db.ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_id: int = db.Column(
        db.Integer, db.ForeignKey("game.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rom_file_id: int = db.Column(
        db.Integer, db.ForeignKey("rom_file.id"), nullable=False, index=True
    )
    # The order it was made for, when the bench named one.
    maintenance_record_id: int | None = db.Column(
        db.Integer, db.ForeignKey("maintenance_record.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    label: str = db.Column(db.String(40), nullable=False)
    part: str = db.Column(db.String(64), nullable=False)
    size_bytes: int = db.Column(db.Integer, nullable=False)
    crc32: str = db.Column(db.String(8), nullable=False)
    sha1: str = db.Column(db.String(40), nullable=False)
    # What `mame -romident` said, as the Pi sent it: null (MAME didn't run), or a match flag
    # with the sets and ROMs it matched.
    romident = db.Column(db.JSON, nullable=True)
    reads: int = db.Column(db.Integer, nullable=False)
    identical: bool = db.Column(db.Boolean, nullable=False)
    blank: bool = db.Column(db.Boolean, nullable=False, default=False)
    taken: datetime = db.Column(db.DateTime, nullable=False)
    clock: str | None = db.Column(db.String(12), nullable=True)
    created: datetime = db.Column(db.DateTime, default=_now, nullable=False)

    game = db.relationship("Game", backref=db.backref("rom_dumps", lazy=True))
    file = db.relationship("RomFile")
    order = db.relationship("MaintenanceRecord", backref=db.backref("rom_dumps", lazy=True))


class RomBurn(db.Model):
    """One attempt to write an image to a chip, verified or stopped (contract "burn")."""

    __tablename__ = "rom_burn"

    id: int = db.Column(db.Integer, primary_key=True)
    uid: str = db.Column(db.String(32), unique=True, nullable=False)
    device_id: int = db.Column(
        db.Integer, db.ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_id: int = db.Column(
        db.Integer, db.ForeignKey("game.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rom_file_id: int = db.Column(
        db.Integer, db.ForeignKey("rom_file.id"), nullable=False, index=True
    )
    maintenance_record_id: int | None = db.Column(
        db.Integer, db.ForeignKey("maintenance_record.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    image_sha1: str = db.Column(db.String(40), nullable=False)
    part: str = db.Column(db.String(64), nullable=False)
    size_bytes: int = db.Column(db.Integer, nullable=False)
    state: str = db.Column(db.String(8), nullable=False)
    vpp: str | None = db.Column(db.String(20), nullable=True)
    blank_ok: bool | None = db.Column(db.Boolean, nullable=True)
    error: str | None = db.Column(db.Text, nullable=True)
    burned: datetime = db.Column(db.DateTime, nullable=False)
    created: datetime = db.Column(db.DateTime, default=_now, nullable=False)

    game = db.relationship("Game", backref=db.backref("rom_burns", lazy=True))
    file = db.relationship("RomFile")
    order = db.relationship("MaintenanceRecord", backref=db.backref("rom_burns", lazy=True))
