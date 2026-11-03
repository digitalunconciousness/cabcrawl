"""ROM images from GATBOX: the ``rom_dump`` and ``burn`` items, and reading them back
(contract v1, capability roms).

The hub stores what the Pi computed and has looked at the bytes: a dump or a burn is accepted
only when the image it names is already stored here, and its size, SHA-1 and (for a dump)
CRC32 are what the stored file computes to. Items follow the ingest rules of the rest of the
contract: ``created`` or ``duplicate`` or ``rejected`` with a reason, one bad item leaving the
others alone, nothing written for a rejected one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.extensions import db
from app.models import BURN_STATES, MaintenanceRecord, RomBurn, RomDump
from app.services import rom_files

UID = "0123456789abcdef"
MAX_TEXT = 2000
ROM_CLOCKS = ("ntp", "rtc", "unverified", "unknown")
NOT_STORED = "the image isn't on the hub: send the file first (POST /api/v1/files)"
NOT_ALLOWED = "machine {slug!r} is not on this device's list"


class Refused(Exception):
    """One item is refused, with the reason the Pi will log."""


def _hex(value, field: str, length: int) -> str:
    if not isinstance(value, str) or len(value) != length or any(c not in UID for c in value):
        raise Refused(f"{field} must be {length} lowercase hex characters")
    return value


def _text(value, field: str, limit: int, required: bool = False):
    if value is None:
        if required:
            raise Refused(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise Refused(f"{field} must be text")
    if len(value) > limit:
        raise Refused(f"{field} is longer than {limit} characters")
    if required and not value:
        raise Refused(f"{field} is required")
    return value


def _count(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_000_000_000:
        raise Refused(f"{field} must be a whole number")
    return value


def _epoch(value, field: str) -> datetime:
    """An epoch from the wire as naive UTC, like every other column here (see rail.py)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not 0 <= value <= 4_102_444_800:
        raise Refused(f"{field} is required, as epoch seconds")
    return datetime.fromtimestamp(float(value), timezone.utc).replace(tzinfo=None)


def _to_epoch(moment: datetime) -> float:
    return moment.replace(tzinfo=timezone.utc).timestamp()


def _romident(value):
    """What ``mame -romident`` said, checked and kept as the Pi sent it."""
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("match"), bool):
        raise Refused("romident must be null or an object with a true or false match")
    out = {"match": value["match"]}
    if value["match"]:
        found = value.get("matches")
        if not isinstance(found, list) or len(found) > 20:
            raise Refused("romident.matches must be a list of at most 20")
        out["matches"] = []
        for m in found:
            if not isinstance(m, dict):
                raise Refused("each romident match must be an object")
            one = {"set": _text(m.get("set"), "romident set", 64, required=True),
                   "rom": _text(m.get("rom"), "romident rom", 64, required=True)}
            if m.get("description") is not None:
                one["description"] = _text(m["description"], "romident description", 200)
            out["matches"].append(one)
    return out


def _machine(device, item, games):
    slug = item.get("machine")
    game = games.get(slug)
    if game is None:
        raise Refused(f"no machine with slug {slug!r}")
    if not device.allows(game):
        raise Refused(NOT_ALLOWED.format(slug=slug))
    return game


def _order_link(item, game):
    """The optional ``order`` of a dump or a burn: one of this machine's orders, or a refusal.
    Never dropped quietly: a link that disappears is worse than a retry."""
    order_id = item.get("order")
    if order_id is None:
        return None
    if isinstance(order_id, bool) or not isinstance(order_id, int):
        raise Refused("order must be the hub's integer id for the record")
    order = db.session.get(MaintenanceRecord, order_id)
    if order is None:
        raise Refused(f"no order with id {order_id}")
    if order.game_id != game.id:
        raise Refused("that order is for a different machine")
    return order.id


def _image(sha256: str, size: int, sha1: str, crc32: str | None = None):
    """The stored file a dump or a burn names, checked against what the Pi computed."""
    image = rom_files.stored(sha256)
    if image is None:
        raise Refused(NOT_STORED)
    if size != image.size_bytes:
        raise Refused(f"size {size} isn't the stored file's ({image.size_bytes})")
    try:
        have_sha1, have_crc = rom_files.digests(sha256)
    except OSError:
        raise Refused("the stored image can't be read: send the file again") from None
    if have_sha1 != sha1:
        raise Refused("sha1 isn't the stored file's")
    if crc32 is not None and have_crc != crc32:
        raise Refused("crc32 isn't the stored file's")
    return image


def _size(item) -> int:
    size = _count(item.get("size"), "size")
    if size < 1:
        raise Refused("size must be 1 or more")
    return size


def _uid(item) -> str:
    return _hex(item.get("uid"), "uid", 32)


def ingest_dump(device, item, games) -> dict:
    out = {"uid": item.get("uid"), "kind": "rom_dump"}
    try:
        uid = _uid(item)
        game = _machine(device, item, games)
        label = _text(item.get("label"), "label", 40, required=True)
        part = _text(item.get("part"), "part", 64, required=True)
        sha1, crc32 = _hex(item.get("sha1"), "sha1", 40), _hex(item.get("crc32"), "crc32", 8)
        sha256 = _hex(item.get("sha256"), "sha256", 64)
        want = hashlib.sha256(
            f"{device.public_id}|rom_dump|{item['machine']}|{label}|{sha1}".encode()
        ).hexdigest()[:32]
        if uid != want:
            raise Refused("uid doesn't follow the contract's rule for this device, machine, "
                          "label and sha1")
        size = _size(item)
        reads = _count(item.get("reads"), "reads")
        if reads < 2:
            raise Refused("reads must be 2 or more")
        if item.get("identical") is not True:
            raise Refused("a dump is only sent when its reads were identical")
        blank = item.get("blank", False)
        if not isinstance(blank, bool):
            raise Refused("blank must be true or false")
        taken = _epoch(item.get("created"), "created")
        clock = item.get("clock")
        source = clock.get("source") if isinstance(clock, dict) else None
        if source is not None and source not in ROM_CLOCKS:
            raise Refused(f"clock.source must be one of {', '.join(ROM_CLOCKS)}")
        romident = _romident(item.get("romident"))

        # The uid hashes this device's public id, so only this device can have made it.
        if RomDump.query.filter_by(uid=uid).first() is not None:
            return {**out, "status": "duplicate"}
        order_id = _order_link(item, game)
        image = _image(sha256, size, sha1, crc32)
    except Refused as why:
        return {**out, "status": "rejected", "reason": str(why)}
    db.session.add(RomDump(
        uid=uid, device_id=device.id, game_id=game.id, rom_file_id=image.id,
        maintenance_record_id=order_id, label=label, part=part, size_bytes=size, crc32=crc32,
        sha1=sha1, romident=romident, reads=reads, identical=True, blank=blank, taken=taken,
        clock=source,
    ))
    return {**out, "status": "created"}


def ingest_burn(device, item, games) -> dict:
    out = {"uid": item.get("uid"), "kind": "burn"}
    try:
        uid = _uid(item)
        game = _machine(device, item, games)
        sha256 = _hex(item.get("image"), "image", 64)
        sha1 = _hex(item.get("image_sha1"), "image_sha1", 40)
        part = _text(item.get("part"), "part", 64, required=True)
        size = _size(item)
        state = item.get("state")
        if state not in BURN_STATES:
            raise Refused(f"state must be one of {', '.join(BURN_STATES)}")
        vpp = _text(item.get("vpp"), "vpp", 20)
        blank_ok = item.get("blank_ok")
        if blank_ok is not None and not isinstance(blank_ok, bool):
            raise Refused("blank_ok must be true, false or null")
        error = _text(item.get("error"), "error", MAX_TEXT)
        burned = _epoch(item.get("created"), "created")
        want = hashlib.sha256(
            f"{device.public_id}|burn|{item['machine']}|{sha1}|{item['created']:.3f}".encode()
        ).hexdigest()[:32]
        if uid != want:
            raise Refused("uid doesn't follow the contract's rule for this device, machine, "
                          "image_sha1 and created")

        if RomBurn.query.filter_by(uid=uid).first() is not None:
            return {**out, "status": "duplicate"}
        order_id = _order_link(item, game)
        image = _image(sha256, size, sha1)
    except Refused as why:
        return {**out, "status": "rejected", "reason": str(why)}
    db.session.add(RomBurn(
        uid=uid, device_id=device.id, game_id=game.id, rom_file_id=image.id,
        maintenance_record_id=order_id, image_sha1=sha1, part=part, size_bytes=size,
        state=state, vpp=vpp, blank_ok=blank_ok, error=error, burned=burned,
    ))
    return {**out, "status": "created"}


def for_game(game_id: int):
    """([dump], [burn]) of one machine, newest first, for its page and the read-back."""
    dumps = RomDump.query.filter_by(game_id=game_id).order_by(
        RomDump.taken.desc(), RomDump.id.desc()).all()
    burns = RomBurn.query.filter_by(game_id=game_id).order_by(
        RomBurn.burned.desc(), RomBurn.id.desc()).all()
    return dumps, burns


def for_order(order_id: int):
    dumps = RomDump.query.filter_by(maintenance_record_id=order_id).order_by(
        RomDump.taken.desc(), RomDump.id.desc()).all()
    burns = RomBurn.query.filter_by(maintenance_record_id=order_id).order_by(
        RomBurn.burned.desc(), RomBurn.id.desc()).all()
    return dumps, burns


def machine_dumps(game) -> dict:
    """GET /machines/<slug>/dumps: the items as the hub holds them, so a rebuilt GATBOX can
    find its machine's dumps and burns again."""
    dumps, burns = for_game(game.id)

    def linked(item, record):
        if record.maintenance_record_id is not None:
            item["order"] = record.maintenance_record_id
        return item

    return {
        "machine": game.barcode,
        "dumps": [linked({
            "kind": "rom_dump", "uid": d.uid, "machine": game.barcode, "label": d.label,
            "part": d.part, "size": d.size_bytes, "crc32": d.crc32, "sha1": d.sha1,
            "sha256": d.file.sha256, "romident": d.romident, "reads": d.reads,
            "identical": d.identical, "blank": d.blank, "created": _to_epoch(d.taken),
            "clock": {"source": d.clock or "unknown"}}, d) for d in dumps],
        "burns": [linked({
            "kind": "burn", "uid": b.uid, "machine": game.barcode, "image": b.file.sha256,
            "image_sha1": b.image_sha1, "part": b.part, "size": b.size_bytes,
            "state": b.state, "vpp": b.vpp, "blank_ok": b.blank_ok, "error": b.error,
            "created": _to_epoch(b.burned)}, b) for b in burns],
    }
