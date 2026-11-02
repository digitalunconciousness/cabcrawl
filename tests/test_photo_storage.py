"""Work-order photos are behind the login (owner's fix, 2026-10-28).

They were saved in static/maintenance_photos/ and served by Flask's static route to anyone
holding the address. Now they are saved in uploads/maintenance_photos/ and served by
maintenance.photo_file, which needs a session; the old address only redirects there, and the
old folder is still read until scripts/move_maintenance_photos.py has moved its files.
Everything here runs on the synthetic arcade with folders in a temporary directory.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "characterization"))
from char_support import login
from seed import seed_floor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "maintenance_1_0a1b2c3d.jpg"
OLD_NAME = "maintenance_1_deadbeef.png"       # a pre-4.4 photo kept its uploaded extension
JPEG = b"\xff\xd8\xff\xe0synthetic photo bytes"


@pytest.fixture()
def floor(app):
    return seed_floor()


@pytest.fixture()
def folders(app, tmp_path, monkeypatch):
    """Point the upload and static folders into tmp_path. Returns (new, old) photo folders."""
    static = tmp_path / "static"
    (static / "maintenance_photos").mkdir(parents=True)
    (static / "css").mkdir()
    (static / "css" / "site.css").write_text("body{}")
    uploads = tmp_path / "uploads"
    (uploads / "maintenance_photos").mkdir(parents=True)
    monkeypatch.setattr(app, "static_folder", str(static))
    app.config["UPLOAD_FOLDER"] = str(uploads)
    return uploads / "maintenance_photos", static / "maintenance_photos"


def _attach(order_id, *names):
    from app.extensions import db
    from app.models import MaintenanceRecord

    order = db.session.get(MaintenanceRecord, order_id)
    for name in names:
        order.add_photo(name)
    db.session.commit()


# --- the new route ------------------------------------------------------------------------------

def test_an_anonymous_visitor_is_sent_to_the_login_page(client, floor, folders):
    new, _old = folders
    (new / NAME).write_bytes(JPEG)
    resp = client.get(f"/maintenance/photos/{NAME}")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert JPEG not in resp.data


@pytest.mark.parametrize("role", ["readonly", "operator", "admin"])
def test_anyone_signed_in_gets_the_photo(client, floor, folders, role):
    """Whoever can open a work order (maintenance_detail is login-only) sees its photos."""
    new, _old = folders
    (new / NAME).write_bytes(JPEG)
    login(client, role)
    resp = client.get(f"/maintenance/photos/{NAME}")
    assert resp.status_code == 200 and resp.data == JPEG
    assert resp.mimetype == "image/jpeg"
    assert "private" in resp.headers["Cache-Control"]      # no shared (Cloudflare) copy


def test_a_photo_not_yet_moved_is_served_from_the_old_folder(client, floor, folders):
    _new, old = folders
    (old / OLD_NAME).write_bytes(b"old png bytes")
    login(client, "readonly")
    resp = client.get(f"/maintenance/photos/{OLD_NAME}")
    assert resp.status_code == 200 and resp.data == b"old png bytes"


def test_the_new_folder_wins_when_both_hold_the_name(client, floor, folders):
    new, old = folders
    (new / NAME).write_bytes(JPEG)
    (old / NAME).write_bytes(b"stale")
    login(client, "readonly")
    assert client.get(f"/maintenance/photos/{NAME}").data == JPEG


@pytest.mark.parametrize("path", [
    "/maintenance/photos/..%2F..%2Fstatic%2Fcss%2Fsite.css",
    "/maintenance/photos/..%2Fmaintenance_photos%2F" + NAME,
    "/maintenance/photos/notes.txt",                     # in the folder, not a photo's name
    "/maintenance/photos/maintenance_1_0a1b2c3d.jpg.txt",
    "/maintenance/photos/maintenance_x_0a1b2c3d.jpg",
    "/maintenance/photos/maintenance_1_missing0.jpg",    # the right shape, not on disk
])
def test_only_a_photo_name_on_disk_is_served(client, floor, folders, path):
    new, _old = folders
    (new / NAME).write_bytes(JPEG)
    (new / "notes.txt").write_text("not a photo")
    (new / "maintenance_1_0a1b2c3d.jpg.txt").write_text("not a photo")
    login(client, "admin")
    resp = client.get(path)
    assert resp.status_code == 404
    assert b"body{}" not in resp.data and b"not a photo" not in resp.data


# --- the old address ----------------------------------------------------------------------------

def test_the_old_address_wins_over_the_static_route(app):
    """Werkzeug tries the rule with more fixed text first; this pins that it still does."""
    adapter = app.url_map.bind("localhost")
    endpoint, args = adapter.match(f"/static/maintenance_photos/{NAME}")
    assert endpoint == "maintenance.legacy_photo" and args == {"filename": NAME}
    assert adapter.match("/static/css/site.css")[0] == "static"


def test_the_old_address_redirects_to_the_new_one(client, floor, folders):
    _new, old = folders
    (old / NAME).write_bytes(JPEG)
    resp = client.get(f"/static/maintenance_photos/{NAME}")
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/maintenance/photos/{NAME}"
    assert JPEG not in resp.data
    # Signed out, the redirect ends at the login page; signed in, at the photo.
    final = client.get(f"/static/maintenance_photos/{NAME}", follow_redirects=True)
    assert JPEG not in final.data and final.request.path == "/login"
    login(client, "readonly")
    final = client.get(f"/static/maintenance_photos/{NAME}", follow_redirects=True)
    assert final.status_code == 200 and final.data == JPEG


@pytest.mark.parametrize("path", [
    f"/static/css/../maintenance_photos/{NAME}",
    f"/static/./maintenance_photos/{NAME}",
    f"/static/css/..%2Fmaintenance_photos%2F{NAME}",
])
def test_other_spellings_of_the_old_folder_are_not_served_publicly(client, floor, folders,
                                                                   path):
    _new, old = folders
    (old / NAME).write_bytes(JPEG)
    resp = client.get(path)
    assert JPEG not in resp.data
    assert resp.status_code in (302, 308, 404)
    if resp.status_code == 302:
        assert resp.headers["Location"].endswith(f"/maintenance/photos/{NAME}")


def test_a_file_in_the_old_folder_that_is_not_a_photo_is_not_served(client, floor, folders):
    _new, old = folders
    (old / "notes.txt").write_text("not a photo")
    resp = client.get("/static/maintenance_photos/notes.txt")
    assert resp.status_code == 404 and b"not a photo" not in resp.data


def test_the_rest_of_static_is_still_served(client, folders):
    assert client.get("/static/css/site.css").data == b"body{}"


# --- saving, showing, deleting ------------------------------------------------------------------

def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 200, 90)).save(buf, "PNG")
    buf.seek(0)
    return buf


def test_a_new_photo_is_saved_in_uploads_and_shown_through_the_new_route(client, floor,
                                                                         folders):
    from app.extensions import db
    from app.models import MaintenanceRecord

    new, old = folders
    login(client, "operator")
    client.post(f"/maintenance_photos/{floor['open_order']}",
                data={"photos": [(_png(), "board.png")]}, content_type="multipart/form-data")
    db.session.expire_all()
    [name] = db.session.get(MaintenanceRecord, floor["open_order"]).get_photos()
    assert (new / name).is_file() and list(old.iterdir()) == []
    html = client.get(f"/maintenance_detail/{floor['open_order']}").get_data(as_text=True)
    assert f'src="/maintenance/photos/{name}"' in html
    assert "/static/maintenance_photos/" not in html
    page = client.get(f"/maintenance_photos/{floor['open_order']}").get_data(as_text=True)
    assert f'src="/maintenance/photos/{name}"' in page


def test_deleting_a_photo_not_yet_moved_removes_it_from_the_old_folder(client, floor, folders):
    _new, old = folders
    (old / OLD_NAME).write_bytes(b"old")
    _attach(floor["open_order"], OLD_NAME)
    login(client, "manager")
    client.post(f"/delete_maintenance_photo/{floor['open_order']}/{OLD_NAME}")
    assert not (old / OLD_NAME).exists()


def test_the_storage_page_counts_both_folders_and_says_what_is_left_to_move(client, floor,
                                                                           folders):
    new, old = folders
    (new / NAME).write_bytes(b"x" * 2048)
    (old / OLD_NAME).write_bytes(b"x" * 2048)
    _attach(floor["open_order"], NAME, OLD_NAME)
    login(client, "admin")
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "1 photo file still in the old folder" in html
    from app.services.storage import photo_stats

    stats = photo_stats()
    assert (stats.files, stats.referenced, stats.unmoved) == (2, 2, 1)


def test_the_storage_cap_counts_the_old_folder_too(app, client, floor, folders):
    _new, old = folders
    (old / "maintenance_9_00000000.jpg").write_bytes(b"x" * (2 * 1024 * 1024))
    app.config["MAX_STORAGE_MB"] = 1
    login(client, "operator")
    client.post(f"/maintenance_photos/{floor['open_order']}",
                data={"photos": [(_png(), "board.png")]}, content_type="multipart/form-data")
    from char_support import flashes

    assert any("Photo storage is full" in m for _, m in flashes(client))


# --- scripts/move_maintenance_photos.py ---------------------------------------------------------

@pytest.fixture()
def mover():
    spec = importlib.util.spec_from_file_location(
        "move_maintenance_photos", os.path.join(ROOT, "scripts", "move_maintenance_photos.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def checkout(tmp_path):
    repo = tmp_path / "checkout"
    old = repo / "static" / "maintenance_photos"
    old.mkdir(parents=True)
    (repo / "uploads").mkdir()
    (old / NAME).write_bytes(JPEG)
    (old / OLD_NAME).write_bytes(b"old png")
    (old / "maintenance_2_11111111.jpg").write_bytes(b"clash, old copy")
    (old / "README.txt").write_text("not a photo")
    new = repo / "uploads" / "maintenance_photos"
    new.mkdir()
    (new / "maintenance_2_11111111.jpg").write_bytes(b"clash, new copy")
    return repo, old, new


def test_the_script_and_the_app_agree_on_a_photo_name(mover):
    from app.services.storage import PHOTO_NAME

    assert mover.PHOTO_NAME.pattern == PHOTO_NAME.pattern
    assert mover.PHOTO_NAME.flags == PHOTO_NAME.flags


def test_a_dry_run_changes_nothing(mover, checkout, capsys):
    repo, old, new = checkout
    before = sorted(os.listdir(old)), sorted(os.listdir(new))
    assert mover.main(["--repo", str(repo)]) == 0
    assert (sorted(os.listdir(old)), sorted(os.listdir(new))) == before
    out = capsys.readouterr().out
    assert "Would move 2 photo(s). 1 already there, 1 other file(s). Nothing was changed" in out


def test_apply_moves_and_never_overwrites(mover, checkout, capsys):
    repo, old, new = checkout
    assert mover.main(["--repo", str(repo), "--apply"]) == 0
    assert (new / NAME).read_bytes() == JPEG and not (old / NAME).exists()
    assert (new / OLD_NAME).read_bytes() == b"old png"
    assert (new / "maintenance_2_11111111.jpg").read_bytes() == b"clash, new copy"
    assert (old / "maintenance_2_11111111.jpg").read_bytes() == b"clash, old copy"
    assert (old / "README.txt").exists()
    out = capsys.readouterr().out
    assert "Moved 2 photo(s). Kept 1 already there, left 1 other file(s), 0 failed." in out
    assert "kept both: maintenance_2_11111111.jpg" in out
    # A second run finds nothing more to move.
    assert mover.main(["--repo", str(repo), "--apply"]) == 0
    assert "Moved 0 photo(s)" in capsys.readouterr().out


def test_apply_makes_the_new_folder_if_needed(mover, checkout):
    repo, _old, new = checkout
    for p in new.iterdir():
        p.unlink()
    new.rmdir()
    assert mover.main(["--repo", str(repo), "--apply"]) == 0
    assert (new / NAME).exists()


def test_a_move_across_filesystems_copies_without_overwriting(mover, checkout, monkeypatch):
    """os.link can't cross filesystems (a Docker volume, say); the fallback copies."""
    import errno

    repo, old, new = checkout

    def no_link(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(mover.os, "link", no_link)
    assert mover.main(["--repo", str(repo), "--apply"]) == 0
    assert (new / NAME).read_bytes() == JPEG and not (old / NAME).exists()
    assert (new / "maintenance_2_11111111.jpg").read_bytes() == b"clash, new copy"
