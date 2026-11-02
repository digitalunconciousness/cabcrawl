"""scripts/backup_uploads.py: what deploy.sh archives beside each database dump.

The dump holds the work orders; the photos and images they point at are files on disk, and
until this nothing backed them up. Everything here runs on an invented checkout in a
temporary folder.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import tarfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "backup_uploads.py")


@pytest.fixture()
def backup():
    spec = importlib.util.spec_from_file_location("backup_uploads", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root, rel, data=b"synthetic"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture()
def checkout(tmp_path):
    """An invented checkout: uploads, both photo folders, and things that must stay out."""
    repo = tmp_path / "checkout"
    _write(repo, "uploads/game_raider.jpg", b"cabinet")
    _write(repo, "uploads/part_abc123.jpg", b"belt")
    _write(repo, "uploads/branding/logo.png", b"logo")
    _write(repo, "uploads/maintenance_photos/maintenance_7_0a1b2c3d.jpg", b"new photo")
    _write(repo, "static/maintenance_photos/maintenance_3_deadbeef.jpg", b"old photo")
    _write(repo, "static/profile_pics/user_1.jpg", b"face")
    # Not uploads: code, assets, the database, secrets, backups.
    _write(repo, "static/css/app.css", b"body{}")
    _write(repo, "instance/arcade.db", b"SQLite format 3")
    _write(repo, ".env", b"SECRET_KEY=not-a-real-secret")
    _write(repo, "backups/arcade_backup_1.db", b"old")
    _write(repo, "app/__init__.py", b"")
    outside = _write(tmp_path, "elsewhere/secret.txt", b"outside the checkout")
    os.symlink(outside, repo / "uploads" / "link.txt")
    os.symlink(outside.parent, repo / "uploads" / "linked-dir")
    return repo


def test_the_upload_folders_are_the_sources(backup, checkout):
    assert backup.backup_sources(str(checkout)) == [
        "uploads", "static/maintenance_photos", "static/profile_pics"]


def test_a_missing_folder_is_skipped(backup, checkout):
    for p in (checkout / "static" / "maintenance_photos").iterdir():
        p.unlink()
    (checkout / "static" / "maintenance_photos").rmdir()
    assert backup.backup_sources(str(checkout)) == ["uploads", "static/profile_pics"]


def test_only_upload_files_are_archived_and_links_are_not_followed(backup, checkout):
    files = {m for m in backup.archive_members(str(checkout))
             if os.path.isfile(checkout / m)}
    assert files == {
        "uploads/game_raider.jpg", "uploads/part_abc123.jpg", "uploads/branding/logo.png",
        "uploads/maintenance_photos/maintenance_7_0a1b2c3d.jpg",
        "static/maintenance_photos/maintenance_3_deadbeef.jpg",
        "static/profile_pics/user_1.jpg",
    }


def test_the_archive_restores_into_an_empty_checkout(backup, checkout, tmp_path):
    out = tmp_path / "arcade_tracker-uploads-20261028T120000Z.tar.gz"
    assert backup.main(["--repo", str(checkout), "--out", str(out)]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    restored = tmp_path / "restored"
    restored.mkdir()
    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert all(not n.startswith(("/", "..")) for n in names)
        assert not any(m.issym() or m.islnk() for m in tar.getmembers())
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(restored, **safe)        # the members were checked just above
    assert (restored / "uploads/maintenance_photos/maintenance_7_0a1b2c3d.jpg").read_bytes() \
        == b"new photo"
    assert (restored / "static/maintenance_photos/maintenance_3_deadbeef.jpg").read_bytes() \
        == b"old photo"
    assert not (restored / "instance").exists() and not (restored / ".env").exists()
    assert not (restored / "static" / "css").exists()


def test_an_existing_archive_is_never_overwritten(backup, checkout, tmp_path):
    out = tmp_path / "taken.tar.gz"
    out.write_bytes(b"keep me")
    assert backup.main(["--repo", str(checkout), "--out", str(out)]) == 2
    assert out.read_bytes() == b"keep me"


def test_deploy_archives_the_uploads_beside_each_dump_and_prunes_them_alike():
    """deploy.sh is what runs in production (deploy/RUNBOOK.md, "Routine deploy")."""
    with open(os.path.join(ROOT, "deploy", "deploy.sh"), encoding="utf-8") as fh:
        text = fh.read()
    assert 'scripts/backup_uploads.py --repo "$REPO" --out "$uploads"' in text
    assert 'uploads="$BACKUPS/arcade_tracker-uploads-$stamp.tar.gz"' in text
    assert '"$BACKUPS"/arcade_tracker-uploads-*.tar.gz' in text
    assert text.index("backup_uploads.py") < text.index('step "Pulling')
