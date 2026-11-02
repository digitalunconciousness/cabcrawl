"""Adding, editing and deleting machines, and recording their coin-meter readings.

Every value from a form is checked here, so a bad one is shown back on the form instead of
becoming a 500 (F-12) or a silent default.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass

from app.extensions import db
from app.models import (
    Game,
    InventoryRequest,
    MaintenanceInventoryUsage,
    MaintenanceRecord,
    PlayRecord,
    RailSession,
    RailSessionTag,
    WorkLog,
    item_game_compatibility,
)

LOCATIONS = ("Floor", "Warehouse", "Shipped")
STATUSES = ("Working", "Being_Fixed", "Not_Working", "Retired")
COUNTER_STATUSES = {"Working": "Working counter", "No_Counter": "No counter",
                    "Broken_Counter": "Broken counter"}
FIRST_YEAR = 1950
NAME_MAX = 100


def _int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def parse_machine_form(form, today: dt.date | None = None) -> tuple[dict, dict[str, str]]:
    """Read the add/edit form. Returns (values to save, errors by field)."""
    today = today or dt.date.today()
    v: dict = {key: (form.get(key) or "").strip() for key in (
        "name", "manufacturer", "genre", "floor_position", "warehouse_section",
        "counter_notes", "notes")}
    errors: dict[str, str] = {}

    if not v["name"]:
        errors["name"] = "Name the machine."
    elif len(v["name"]) > NAME_MAX:
        errors["name"] = f"Keep the name under {NAME_MAX} characters."

    try:
        v["year"] = _int(form.get("year"))
        if v["year"] is not None and not FIRST_YEAR <= v["year"] <= today.year + 1:
            errors["year"] = f"Use a year between {FIRST_YEAR} and {today.year + 1}, or leave it blank."
    except ValueError:
        v["year"] = None
        errors["year"] = "The year must be a number, like 1981."

    for key, allowed in (("location", LOCATIONS), ("status", STATUSES),
                         ("counter_status", tuple(COUNTER_STATUSES))):
        v[key] = form.get(key) or allowed[0]
        if v[key] not in allowed:
            errors[key] = "Pick one from the list."

    raw_price = (form.get("coins_per_play") or "").strip()
    try:
        v["coins_per_play"] = float(raw_price) if raw_price else 0.25
        if v["coins_per_play"] < 0:
            errors["coins_per_play"] = "The price can't be negative."
    except ValueError:
        v["coins_per_play"] = 0.25
        errors["coins_per_play"] = "The price must be a number, like 0.25."

    try:
        v["initial_coin_count"] = _int(form.get("initial_coin_count"))
        if v["initial_coin_count"] is not None and v["initial_coin_count"] < 0:
            errors["initial_coin_count"] = "The meter reading can't be negative."
    except ValueError:
        v["initial_coin_count"] = None
        errors["initial_coin_count"] = "The meter reading must be a whole number."
    return v, errors


GAME_FIELDS = ("name", "manufacturer", "year", "genre", "location", "floor_position",
               "warehouse_section", "status", "coins_per_play", "counter_status",
               "counter_notes", "notes")


def apply_machine_values(game: Game, v: dict) -> None:
    for key in GAME_FIELDS:
        value = v.get(key)
        setattr(game, key, value if value != "" else None)


def has_play_history(game: Game) -> bool:
    """True once any reading recorded plays: then the baseline can no longer change."""
    return PlayRecord.query.filter(PlayRecord.game_id == game.id,
                                   PlayRecord.plays_count > 0).count() > 0


def set_baseline(game: Game, coin_count: int, note: str) -> str:
    """Set or replace the starting meter reading of a machine with no plays yet."""
    existing = PlayRecord.query.filter_by(game_id=game.id).order_by(PlayRecord.id).first()
    if existing is not None:
        existing.coin_count = coin_count
        existing.date_recorded = dt.date.today()
        existing.notes = note
        return "Updated"
    db.session.add(PlayRecord(game_id=game.id, coin_count=coin_count, plays_count=0,
                              revenue=0.0, date_recorded=dt.date.today(), notes=note))
    return "Set"


def save_machine_image(upload, upload_folder: str) -> tuple[str | None, str | None]:
    """Save a cabinet photo, re-encoded as JPEG. Returns (filename, problem)."""
    from app.utils.helpers import allowed_file, compress_and_save_image

    if not upload or not upload.filename:
        return None, None
    if not allowed_file(upload.filename):
        return None, "That isn't a photo. Use PNG, JPG or GIF."
    os.makedirs(upload_folder, exist_ok=True)
    name = f"machine_{uuid.uuid4().hex[:12]}.jpg"
    path = os.path.join(upload_folder, name)
    if not compress_and_save_image(upload, path):
        if os.path.exists(path):
            os.remove(path)
        return None, f"{upload.filename} could not be read as an image."
    return name, None


# --- coin-meter readings -------------------------------------------------------------------

@dataclass
class Reading:
    coin_count: int | None
    date: dt.date | None
    notes: str


def parse_reading(form, last_count: int, today: dt.date | None = None
                  ) -> tuple[Reading, dict[str, str]]:
    """Check a coin-meter reading. A missing or malformed date used to be a 500 (F-12)."""
    today = today or dt.date.today()
    errors: dict[str, str] = {}
    try:
        count = _int(form.get("coin_count"))
        if count is None:
            errors["coin_count"] = "Enter the number on the coin meter."
        elif count < last_count:
            errors["coin_count"] = (f"The meter can't go backwards: the last reading was "
                                    f"{last_count:,}.")
    except ValueError:
        count = None
        errors["coin_count"] = "The meter reading must be a whole number."

    raw_date = (form.get("date") or "").strip()
    try:
        when = dt.date.fromisoformat(raw_date) if raw_date else today
        if when > today:
            errors["date"] = "The date can't be in the future."
    except ValueError:
        when = None
        errors["date"] = "Pick a date, like 2026-10-24."
    return Reading(count, when, (form.get("notes") or "").strip()), errors


def record_reading(game: Game, reading: Reading, last_count: int) -> PlayRecord:
    plays = reading.coin_count - last_count
    revenue = plays * (game.coins_per_play or 0)
    record = PlayRecord(game_id=game.id, coin_count=reading.coin_count, plays_count=plays,
                        revenue=revenue, date_recorded=reading.date, notes=reading.notes)
    db.session.add(record)
    game.total_plays = (game.total_plays or 0) + plays
    game.total_revenue = (game.total_revenue or 0) + revenue
    return record


def last_coin_count(game: Game) -> int:
    last = (PlayRecord.query.filter_by(game_id=game.id)
            .order_by(PlayRecord.date_recorded.desc(), PlayRecord.id.desc()).first())
    return last.coin_count if last else 0


# --- delete --------------------------------------------------------------------------------

def delete_machine(game: Game, upload_folder: str) -> dict[str, int]:
    """Delete a machine with its play records and work orders, leaving nothing dangling.

    The old view bulk-deleted with ``query.delete()``, which skips the ORM cascades, so the
    orders' work logs, part usage and trace tags were orphaned on SQLite and refused by
    PostgreSQL's foreign keys (F-10). Rail sessions measured on it stay in the rail history
    unlinked, and part requests made for its orders keep their item and lose the order link.
    Caller commits.
    """
    from app.services.storage import photo_folders

    orders = MaintenanceRecord.query.filter_by(game_id=game.id).all()
    order_ids = [o.id for o in orders]
    photos = [p for o in orders for p in o.get_photos()]
    if order_ids:
        for column in (RailSessionTag.maintenance_record_id, WorkLog.maintenance_id,
                       MaintenanceInventoryUsage.maintenance_id):
            column.class_.query.filter(column.in_(order_ids)).delete(
                synchronize_session=False)
        InventoryRequest.query.filter(InventoryRequest.maintenance_id.in_(order_ids)).update(
            {InventoryRequest.maintenance_id: None}, synchronize_session=False)
        MaintenanceRecord.query.filter(MaintenanceRecord.id.in_(order_ids)).delete(
            synchronize_session=False)
    plays = PlayRecord.query.filter_by(game_id=game.id).delete(synchronize_session=False)
    from app.models.history import GameEvent

    GameEvent.query.filter_by(game_id=game.id).delete(synchronize_session=False)
    from app.models.upkeep import MaintenanceTask

    # Its own maintenance tasks go with it; the orders they raised went with its orders.
    MaintenanceTask.query.filter_by(game_id=game.id).delete(synchronize_session=False)
    RailSession.query.filter_by(game_id=game.id).update(
        {RailSession.game_id: None}, synchronize_session=False)
    db.session.execute(item_game_compatibility.delete().where(
        item_game_compatibility.c.game_id == game.id))
    image = game.image_filename
    db.session.expunge(game)
    Game.query.filter_by(id=game.id).delete(synchronize_session=False)

    # The machine's image, and its orders' photos from whichever photo folder holds them.
    for name, folder in [(image, upload_folder)] + [(p, f) for p in photos
                                                     for f in photo_folders()]:
        if name and os.path.basename(name) == name:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    return {"orders": len(order_ids), "plays": plays}
