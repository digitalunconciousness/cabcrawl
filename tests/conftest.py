"""Shared fixtures. Every test gets its own throwaway SQLite database.

Nothing here may touch a real database: DATABASE_URL is overridden for the whole
session before the application is imported, so a stray test cannot reach the
deployment's PostgreSQL even if .env is present.
"""
from __future__ import annotations

import os
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def _isolate_environment():
    """Point the application at a scratch database and a throwaway secret."""
    tmp = tempfile.mkdtemp(prefix="arcade-tracker-tests-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tmp, 'test.db')}"
    os.environ["SECRET_KEY"] = "test-only-not-a-real-secret"
    os.environ.setdefault("WTF_CSRF_ENABLED", "False")
    yield


@pytest.fixture()
def app(_isolate_environment):
    from app import create_app
    from app.extensions import db

    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Flask-Limiter's storage outlives the app fixture.

    ``limiter`` is a module-level singleton with ``storage_uri="memory://"``, so its counters
    are created once per pytest process and shared by every test that follows -- a test that
    exhausts a limit would quietly spend another test's budget, and the order they run in
    would decide who fails.
    """
    from app.extensions import limiter

    # ``limiter.storage`` asserts on an uninitialised limiter, and this runs before the app
    # fixture builds one -- so on the first test of a process there is nothing to reset yet.
    # Checking the attribute is less odd than catching the AssertionError behind it.
    if getattr(limiter, "_storage", None) is not None:
        limiter.reset()
    yield


# --- the game library (contract/library-v1/): a stub archive and an application set up to read it ------------

@pytest.fixture()
def stub():
    from tests.library_stub import Server

    server = Server()
    yield server
    server.stop()


@pytest.fixture()
def deck(app, stub, tmp_path):
    """The application, set up to read the stub, with one machine that names widget-wars and one that names nothing."""
    from app.extensions import db
    from app.models import Game
    from tests.library_stub import TOKEN

    app.config.update(LIBRARY_URL=stub.url, LIBRARY_TOKEN=TOKEN, LIBRARY_ACCESS_ID="", LIBRARY_ACCESS_SECRET="",
                      LIBRARY_FILES_DIR=str(tmp_path / "library_files"), LIBRARY_MAX_FILE=100 * 1024 * 1024)
    ww = Game(name="Widget Wars", barcode="widget-wars-1", status="Working", location="Floor", model_key="widget-wars")
    other = Game(name="Sprocket", barcode="pin-sprocket", status="Working", location="Floor")
    db.session.add_all([ww, other])
    db.session.commit()
    return {"stub": stub, "archive": stub.archive, "machine": ww, "other": other}
