#!/usr/bin/env python3
"""Start the tracker fresh: delete the history, keep the machines.

Deleted:
  - work orders, their work logs, their parts used and their rail-session tags
  - play records (coin-meter readings), and each machine's play, revenue and ranking totals
  - the whole inventory: parts, stock history, low-stock alerts, part requests and their
    history, stock in each place, supplier links and price history, purchase orders, serial
    units and their history, stock counts, reservations, and the parts on maintenance tasks
  - the work-order photo files in uploads/maintenance_photos/ (and any not yet moved out of
    static/maintenance_photos/)

Kept:
  - machines: name, barcode (printed QR labels), coin-door code (printed coin-door labels),
    location, status, counter status, price per play, notes, cabinet photo
  - user accounts, GATBOX device tokens, and the rail voltage history (sessions, readings)
  - each machine's location and status timeline (no longer linked to the deleted work orders)
  - storage places, part categories, suppliers, and maintenance tasks

Run it from the repository root with the application's environment loaded, so DATABASE_URL
points at the real database. Without --yes it only counts what it would delete:

    set -a && . ./.env && set +a
    /opt/arcade-tracker-venv/bin/python scripts/reset_history.py

To delete, first take a backup, then pass it in. The script checks the file is a fresh,
non-trivial dump before it touches anything:

    pg_dump --clean --if-exists --no-owner --no-privileges "$DATABASE_URL" \\
        | gzip -9 > /var/backups/arcade-tracker/pre-reset.sql.gz
    /opt/arcade-tracker-venv/bin/python scripts/reset_history.py \\
        --yes --backup /var/backups/arcade-tracker/pre-reset.sql.gz

The database part is one transaction: it all happens or none of it does. Photo files are
removed only after that commit. To undo, restore the dump the way deploy/RUNBOOK.md says.
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                      # noqa: E402
from app.extensions import db                   # noqa: E402
from app.models import (                        # noqa: E402
    Game,
    GameEvent,
    InventoryItem,
    InventoryRequest,
    InventoryRequestHistory,
    ItemSupplier,
    LowStockAlert,
    MaintenanceInventoryUsage,
    MaintenanceRecord,
    PartReservation,
    PlayRecord,
    PriceChange,
    PurchaseOrder,
    PurchaseOrderLine,
    RailSessionTag,
    SerialUnit,
    StockCount,
    StockCountLine,
    StockHistory,
    StockLevel,
    TaskPart,
    UnitEvent,
    WorkLog,
    item_game_compatibility,
)

# Children before parents, so no foreign key is ever left pointing at a deleted row.
DELETE_ORDER = (
    ("rail-session tags on work orders", RailSessionTag),
    ("part requests: history", InventoryRequestHistory),
    ("part requests", InventoryRequest),
    ("parts reserved for work orders", PartReservation),
    ("serial units: history", UnitEvent),
    ("serial units", SerialUnit),
    ("parts used in work orders", MaintenanceInventoryUsage),
    ("work logs", WorkLog),
    ("work orders", MaintenanceRecord),
    ("play records (coin-meter readings)", PlayRecord),
    ("stock history", StockHistory),
    ("low-stock alerts", LowStockAlert),
    ("stock count lines", StockCountLine),
    ("stock counts", StockCount),
    ("purchase order lines", PurchaseOrderLine),
    ("purchase orders", PurchaseOrder),
    ("price history", PriceChange),
    ("part-to-supplier links", ItemSupplier),
    ("stock in each place", StockLevel),
    ("parts on maintenance tasks", TaskPart),
    ("part-to-machine links", item_game_compatibility),
    ("parts", InventoryItem),
)

BACKUP_MAX_AGE_S = 2 * 60 * 60
BACKUP_MIN_BYTES = 2048


def _count(target) -> int:
    if hasattr(target, "query"):
        return target.query.count()
    return db.session.execute(db.select(db.func.count()).select_from(target)).scalar_one()


def _delete(target) -> None:
    if hasattr(target, "__table__"):
        db.session.execute(db.delete(target))
    else:
        db.session.execute(target.delete())


def backup_problem(path: str | None, now: float | None = None) -> str | None:
    """Why *path* is not a backup this reset may rely on, or None."""
    if not path:
        return "pass --backup with the dump you just took (see the top of this script)"
    if not os.path.isfile(path):
        return f"{path} does not exist"
    size = os.path.getsize(path)
    if size < BACKUP_MIN_BYTES:
        return f"{path} is only {size} bytes, too small to be a real dump"
    age = (now or time.time()) - os.path.getmtime(path)
    if age > BACKUP_MAX_AGE_S:
        return f"{path} is {age / 3600:.1f} hours old; take a fresh one right before the reset"
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as fh:
            head = fh.read(4 * 1024 * 1024)
    except OSError as e:
        return f"{path} could not be read: {e}"
    if "maintenance_record" not in head and "PostgreSQL database dump" not in head:
        return f"{path} does not look like a dump of this database"
    return None


def photo_files(app) -> list[str]:
    """Every file in both work-order photo folders (app/services/storage.py)."""
    from app.services.storage import photo_folders

    out = []
    for folder in photo_folders(app):
        if os.path.isdir(folder):
            out += [os.path.join(folder, name) for name in os.listdir(folder)
                    if os.path.isfile(os.path.join(folder, name))]
    return sorted(out)


def main(argv: list[str] | None = None, app=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="delete (default: only count)")
    parser.add_argument("--backup", help="the pg_dump file taken just before this run")
    args = parser.parse_args(argv)

    app = app or create_app()
    with app.app_context():
        counts = [(label, _count(target)) for label, target in DELETE_ORDER]
        photos = photo_files(app)
        machines = Game.query.count()

        print("Would delete:" if not args.yes else "Deleting:")
        for label, n in counts:
            print(f"  {n:>7}  {label}")
        print(f"  {len(photos):>7}  work-order photo files")
        print(f"Keeping {machines} machines (totals and rankings reset to zero) and their "
              "timelines, users, devices, rail history, storage places, part categories, "
              "suppliers and maintenance tasks.")

        if not args.yes:
            print("\nNothing was changed. Take a backup and re-run with --yes --backup FILE.")
            return 0
        why_not = backup_problem(args.backup)
        if why_not:
            print(f"\nRefusing: {why_not}. Nothing was changed.", file=sys.stderr)
            return 2

        try:
            # The machine timeline stays. PostgreSQL clears its link to a deleted work order
            # itself (ON DELETE SET NULL); SQLite doesn't enforce foreign keys, so do it here.
            db.session.execute(db.update(GameEvent).values(maintenance_id=None))
            for _label, target in DELETE_ORDER:
                _delete(target)
            db.session.execute(db.update(Game).values(
                total_plays=0, total_revenue=0.0, times_in_top_5=0, times_in_top_10=0,
                last_ranking_update=None))
            db.session.commit()
        except Exception:
            db.session.rollback()
            print("\nThe database part failed and was rolled back. Nothing was changed.",
                  file=sys.stderr)
            raise

        removed = 0
        for path in photos:
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                print(f"  could not remove {path}: {e}", file=sys.stderr)
        print(f"\nDone. History cleared for {machines} machines; {removed} photo files removed.")
        print(f"The backup to restore from, if needed: {args.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
