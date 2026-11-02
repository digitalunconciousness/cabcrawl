"""Storage and backups after Step 4.7c: photo cleanup, backup files, PostgreSQL honesty."""
from __future__ import annotations

import os
import time

import pytest

from char_support import flashes, login
from test_routes_post import fresh

TWO_YEARS = 2 * 365 * 86400


def _old(path):
    then = time.time() - TWO_YEARS
    os.utime(path, (then, then))


# --- F-6: cleanup keeps photos an order still shows ----------------------------------------

def test_cleanup_keeps_photos_a_work_order_still_uses(client, floor, sandbox):
    from app.extensions import db
    from app.models import MaintenanceRecord

    photos = sandbox / "uploads" / "maintenance_photos"
    kept, gone, recent = photos / "kept.jpg", photos / "gone.jpg", photos / "recent.jpg"
    for p in (kept, gone, recent):
        p.write_bytes(b"x")
    _old(kept)
    _old(gone)
    fresh(MaintenanceRecord, floor["closed_order"]).add_photo("kept.jpg")
    db.session.commit()
    login(client, "admin")
    client.post("/admin/cleanup_photos")
    assert kept.exists() and recent.exists() and not gone.exists()
    assert ("success", "Removed 1 unused photo.") in flashes(client)


def test_the_storage_page_counts_what_cleanup_would_remove(client, floor, sandbox):
    from app.extensions import db
    from app.models import MaintenanceRecord

    photos = sandbox / "uploads" / "maintenance_photos"
    for name in ("on-order.jpg", "orphan-old.jpg", "orphan-new.jpg"):
        (photos / name).write_bytes(b"x" * 1024)
    _old(photos / "orphan-old.jpg")
    fresh(MaintenanceRecord, floor["open_order"]).add_photo("on-order.jpg")
    db.session.commit()
    login(client, "admin")
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "Remove 1 unused photo" in html
    assert "2 older than a year" not in html and "1 older than a year" in html


def test_managers_no_longer_clean_up_photos(client, floor, sandbox):
    photo = sandbox / "uploads" / "maintenance_photos" / "orphan.jpg"
    photo.write_bytes(b"x")
    _old(photo)
    login(client, "manager")
    client.post("/admin/cleanup_photos")
    assert photo.exists()


# --- F-18 and F-34: backup files -----------------------------------------------------------

@pytest.mark.parametrize("name", ["../instance/precious.db", "/etc/passwd",
                                  "arcade_backup_../../instance/precious.db",
                                  "precious.db", ""])
def test_delete_and_restore_cannot_leave_the_backup_folder(client, floor, sandbox, name):
    victim = sandbox / "instance" / "precious.db"
    victim.write_bytes(b"synthetic")
    login(client, "admin")
    client.post("/delete_backup", data={"backup_file": name})
    client.post("/restore_backup", data={"backup_file": name})
    assert victim.read_bytes() == b"synthetic"


def test_a_backup_can_be_downloaded(client, floor, sandbox):
    (sandbox / "backups" / "arcade_backup_20260101_000000.db").write_bytes(b"SQLite format 3")
    login(client, "admin")
    resp = client.get("/download_backup/arcade_backup_20260101_000000.db")
    assert resp.status_code == 200 and resp.data == b"SQLite format 3"


def test_a_download_cannot_leave_the_backup_folder(client, floor, sandbox):
    login(client, "admin")
    resp = client.get("/download_backup/..%2Finstance%2Farcade.db")
    assert resp.status_code in (302, 404)


def test_the_backup_name_check():
    from app.services.storage import BACKUP_NAME

    assert BACKUP_NAME.match("arcade_backup_20260101_000000.db")
    for bad in ("../arcade_backup_x.db", "arcade_backup_x.db/..", "arcade_backup_.sql",
                "x/arcade_backup_y.db"):
        assert not BACKUP_NAME.match(bad), bad


# --- F-11: on PostgreSQL the page says so and the SQLite buttons are off -------------------

@pytest.fixture()
def on_postgres(monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "database_kind", lambda: "postgresql")


def test_on_postgres_the_page_explains_and_offers_no_sqlite_buttons(
        app, client, floor, sandbox, on_postgres, tmp_path):
    dumps = tmp_path / "deploy-dumps"
    dumps.mkdir()
    (dumps / "arcade_tracker-20261024T074228Z.sql.gz").write_bytes(b"x" * 2048)
    app.config["BACKUPS_DIR"] = str(dumps)          # ARCADE_BACKUPS, read at startup
    login(client, "admin")
    html = client.get("/backup_management").get_data(as_text=True)
    assert "Backups run outside the app" in html
    assert "arcade_tracker-20261024T074228Z.sql.gz" in html
    assert "/create_backup" not in html and "/restore_backup" not in html


def test_on_postgres_create_and_restore_refuse(client, floor, sandbox, on_postgres):
    login(client, "admin")
    client.post("/create_backup")
    assert list((sandbox / "backups").iterdir()) == []
    assert any("only work on a SQLite database" in m for _, m in flashes(client))


def test_an_unreadable_dump_folder_is_explained_not_a_500(client, floor, sandbox, on_postgres,
                                                         monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "list_backups", lambda folder, pattern=None: None)
    login(client, "admin")
    resp = client.get("/backup_management")
    assert resp.status_code == 200
    assert "readable by root only" in resp.get_data(as_text=True)


def test_deploy_dumps_are_dated_and_ordered_by_the_stamp_in_their_name(tmp_path):
    """A copied or restored dump gets a new mtime; the name keeps when it was taken."""
    from app.services.storage import DEPLOY_DUMP_NAME, list_backups

    for stamp in ("20261008T061500Z", "20261024T074228Z", "20261022T164300Z"):
        (tmp_path / f"arcade_tracker-{stamp}.sql.gz").write_bytes(b"x")
    (tmp_path / "pre-reset.sql.gz").write_bytes(b"x")
    found = list_backups(str(tmp_path), DEPLOY_DUMP_NAME)
    assert [b.name[15:31] for b in found] == [
        "20261024T074228Z", "20261022T164300Z", "20261008T061500Z"]
    assert found[0].created.strftime("%Y-%m-%d %H:%M") == "2026-10-24 07:42"
