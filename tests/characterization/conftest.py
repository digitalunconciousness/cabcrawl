"""Characterization fixtures: a small synthetic arcade, one user per role.

Everything here is invented. Machine names, usernames and parts are synthetic and must stay
that way (the repository is public). No test may read ``instance/arcade.db``: the session
fixture in ``tests/conftest.py`` points DATABASE_URL at a scratch file, and the ``sandbox``
fixture below moves every directory a view writes to (photos, uploads, backups, profile
pictures) into a temporary folder, so running the suite leaves the checkout untouched.
"""
from __future__ import annotations

import os
import shutil

import pytest

from char_support import login
from seed import seed_floor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def sandbox(app, tmp_path, monkeypatch):
    """Redirect every directory a view writes to into ``tmp_path``.

    * ``static_folder`` (profile pictures, and work-order photos saved before they moved to
      uploads/) and ``root_path`` (the storage page) are pointed at a copy-free scratch tree.
    * ``UPLOAD_FOLDER`` (game images, part photos, work-order photos) likewise.
    * The working directory becomes ``tmp_path``, with ``scripts/`` copied in, because the
      backup views shell out to ``scripts/*.py`` relative to the cwd and those scripts open
      ``instance/arcade.db`` relative to it. The copy there is a synthetic SQLite file.
    """
    from app.utils import helpers

    fake_root = tmp_path / "app"
    fake_root.mkdir()
    static = tmp_path / "static"
    (static / "maintenance_photos").mkdir(parents=True)
    (static / "profile_pics").mkdir()
    uploads = tmp_path / "uploads"
    (uploads / "maintenance_photos").mkdir(parents=True)
    (tmp_path / "backups").mkdir()
    (tmp_path / "instance").mkdir()
    shutil.copytree(os.path.join(REPO_ROOT, "scripts"), tmp_path / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__", "smoke"))
    (fake_root / "utils").mkdir()

    monkeypatch.setattr(app, "static_folder", str(static))
    monkeypatch.setattr(app, "root_path", str(fake_root))
    app.config["UPLOAD_FOLDER"] = str(uploads)
    monkeypatch.setattr(helpers, "__file__", str(fake_root / "utils" / "helpers.py"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_login_lockouts():
    """The lockout table is a module-level dict; one test's failures must not lock another."""
    from app.security import utils

    utils.failed_login_attempts.clear()
    yield
    utils.failed_login_attempts.clear()


@pytest.fixture()
def floor(app):
    """The synthetic arcade. Returns a dict of ids, so tests never hold detached objects."""
    return seed_floor()


@pytest.fixture()
def as_role(client, floor):
    """``as_role("manager")`` returns the client, signed in as the seeded manager."""
    def _as(role: str):
        return login(client, role)
    return _as
