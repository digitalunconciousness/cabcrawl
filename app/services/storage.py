"""Storage and backups: work-order photos on disk, and database backup files."""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import MaintenanceRecord

PHOTO_MAX_AGE_DAYS = 365

# --- photos --------------------------------------------------------------------------------
#
# Work-order photos live in uploads/maintenance_photos/ (under UPLOAD_FOLDER, beside the
# machine images, part photos and branding), outside static/, so only signed-in users get
# them (maintenance.photo_file). Until 2026-10-28 they were saved in
# static/maintenance_photos/, where Flask's static route served them to anyone with the
# address. That folder is still read, never written, until
# scripts/move_maintenance_photos.py has moved its files, so deploying before the move
# breaks nothing and those photos are behind the login too.

PHOTO_SUBDIR = "maintenance_photos"
# What save_photos names a photo: maintenance_<order id>_<8 hex>.jpg. Photos saved before
# Step 4.4 kept the uploaded extension (docs/history/app.py), so those are accepted too.
# scripts/move_maintenance_photos.py carries a copy (tests/test_photo_storage.py checks it).
PHOTO_NAME = re.compile(r"^maintenance_\d+_[0-9a-f]{8}\.(jpg|jpeg|png|gif)$", re.IGNORECASE)


def photo_folder(app=None) -> str:
    """Where work-order photos are saved: uploads/maintenance_photos/."""
    app = app or current_app
    return os.path.join(app.config["UPLOAD_FOLDER"], PHOTO_SUBDIR)


def legacy_photo_folder(app=None) -> str:
    """static/maintenance_photos/, where photos were saved until 2026-10-28. Read only."""
    app = app or current_app
    return os.path.join(app.static_folder, PHOTO_SUBDIR)


def photo_folders(app=None) -> list[str]:
    """Both photo folders, the one a photo is looked for in first first."""
    return [photo_folder(app), legacy_photo_folder(app)]


def photo_path(name: str, app=None) -> str | None:
    """The file holding photo *name*: the new folder first, then the old one. None for a
    name the app would never give a photo (so nothing else can be reached through it) or a
    photo that is in neither folder."""
    if not name or not PHOTO_NAME.match(name) or os.path.basename(name) != name:
        return None
    for folder in photo_folders(app):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and not os.path.islink(path):
            return path
    return None


def referenced_photos() -> set[str]:
    """Every photo filename some work order still shows."""
    names: set[str] = set()
    for (raw,) in db.session.query(MaintenanceRecord.photos).filter(
            MaintenanceRecord.photos.isnot(None)):
        record = MaintenanceRecord(photos=raw)
        names.update(record.get_photos())
    return names


@dataclass
class PhotoStats:
    size_mb: float
    files: int
    referenced: int
    orphans: int
    old_orphans: int
    orders_with_photos: int
    max_mb: int          # MAX_TOTAL_STORAGE_MB (app/config.py)
    unmoved: int = 0     # files still in the old static folder

    @property
    def percent(self) -> float:
        return min(100.0, self.size_mb / self.max_mb * 100) if self.max_mb else 0.0


def _photo_files(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    return [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))]


def _all_photo_files() -> list[tuple[str, str]]:
    """(folder, name) for every file in both photo folders."""
    return [(folder, name) for folder in photo_folders() for name in _photo_files(folder)]


def photo_storage_mb() -> float:
    """What the photos take up, in both folders: the figure MAX_TOTAL_STORAGE_MB caps."""
    return sum(os.path.getsize(os.path.join(f, n)) for f, n in _all_photo_files()) / (1024 * 1024)


def photo_stats(now: float | None = None) -> PhotoStats:
    files = _all_photo_files()
    used = referenced_photos()
    cutoff = (now or time.time()) - PHOTO_MAX_AGE_DAYS * 86400
    orphans = [(f, n) for f, n in files if n not in used]
    legacy = legacy_photo_folder()
    return PhotoStats(
        size_mb=photo_storage_mb(),
        files=len(files),
        referenced=sum(1 for _f, n in files if n in used),
        orphans=len(orphans),
        old_orphans=sum(1 for f, n in orphans if os.path.getmtime(os.path.join(f, n)) < cutoff),
        orders_with_photos=MaintenanceRecord.query.filter(
            MaintenanceRecord.photos.isnot(None), MaintenanceRecord.photos != "[]").count(),
        max_mb=current_app.config["MAX_STORAGE_MB"],
        unmoved=sum(1 for f, _n in files if f == legacy),
    )


def cleanup_photos(now: float | None = None) -> int:
    """Delete photo files older than a year that no work order shows, in both folders.
    Returns the count.

    It used to delete every file older than a year, so an old order kept the filename and
    its photo 404'd (F-6). A photo an order still lists is kept, however old.
    """
    used = referenced_photos()
    cutoff = (now or time.time()) - PHOTO_MAX_AGE_DAYS * 86400
    removed = 0
    for folder, name in _all_photo_files():
        path = os.path.join(folder, name)
        if name not in used and os.path.getmtime(path) < cutoff:
            os.remove(path)
            removed += 1
    return removed


# --- backups -------------------------------------------------------------------------------

BACKUP_NAME = re.compile(r"^arcade_backup_[A-Za-z0-9_.-]+\.db$")


def database_kind() -> str:
    return db.engine.dialect.name


def in_app_backups_available() -> bool:
    """The in-app backups copy the SQLite file. On PostgreSQL there is no such file, so the
    page says how backups are taken instead of pretending (F-11; owner decision 8)."""
    return database_kind() == "sqlite"


def backup_dir() -> str:
    """Where scripts/backup_database.py writes: ``backups/`` under the working directory.

    Absolute, because ``send_file`` resolves a relative path against the app package, not
    the working directory the views checked, so every download was a 500 (F-34).
    """
    return os.path.abspath("backups")


def backup_path(name: str | None) -> str | None:
    """The path of backup *name* inside backup_dir(), or None if it is not one of ours.

    Only a bare ``arcade_backup_*.db`` name that exists in the folder passes, so a form value
    like ``../instance/arcade.db`` can neither be restored over the database nor deleted
    (F-18).
    """
    if not name or not BACKUP_NAME.match(name) or os.path.basename(name) != name:
        return None
    folder = backup_dir()
    path = os.path.realpath(os.path.join(folder, name))
    if os.path.dirname(path) != os.path.realpath(folder) or not os.path.isfile(path):
        return None
    return path


@dataclass
class BackupFile:
    name: str
    size: int
    created: dt.datetime  # UTC, naive

    @property
    def age_days(self) -> int:
        return (_utc_now() - self.created).days


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


_STAMP = re.compile(r"(\d{8}T\d{6}Z|\d{8}_\d{6})")


def _created(name: str, path: str) -> dt.datetime:
    """When a backup was taken: the stamp in its name (deploy.sh and backup_database.py
    both write one, in UTC and local time respectively), else the file's mtime."""
    found = _STAMP.search(name)
    if found:
        raw = found.group(1)
        fmt = "%Y%m%dT%H%M%SZ" if "T" in raw else "%Y%m%d_%H%M%S"
        try:
            return dt.datetime.strptime(raw, fmt)  # noqa: DTZ007 -- naive UTC, like the DB columns
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(os.path.getmtime(path), dt.UTC).replace(tzinfo=None)


def list_backups(folder: str, pattern: re.Pattern = BACKUP_NAME) -> list[BackupFile] | None:
    """Backups in *folder*, newest first; None when the app may not read the folder (deploy
    keeps its dumps root-only, mode 700)."""
    if not os.path.isdir(folder):
        return []
    out = []
    try:
        names = os.listdir(folder)
    except PermissionError:
        return None
    for name in names:
        path = os.path.join(folder, name)
        if pattern.match(name) and os.path.isfile(path):
            out.append(BackupFile(name, os.path.getsize(path), _created(name, path)))
    return sorted(out, key=lambda b: b.created, reverse=True)


# The dumps deploy/deploy.sh takes before every deploy (PostgreSQL).
DEPLOY_DUMP_NAME = re.compile(r"^arcade_tracker-\d{8}T\d{6}Z\.sql\.gz$")


def deploy_dump_dir() -> str:
    """Where deploy.sh writes its dumps (ARCADE_BACKUPS, read in app/config.py)."""
    return current_app.config["BACKUPS_DIR"]
