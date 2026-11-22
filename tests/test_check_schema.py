"""scripts/check_schema.py -- the sequence and encoding halves.

The schema comparison is exercised every time ``deploy.sh`` runs. These two cannot
be: both are PostgreSQL-only and the suite runs on SQLite. What is tested here is
the dialect guard (neither may fire spuriously), the repair statement the sequence
check prints, and the three encoding states -- because the difference between
"refuse the deploy" and "warn and continue" is the whole value of the check.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _module():
    path = os.path.join(REPO_ROOT, "scripts", "check_schema.py")
    spec = importlib.util.spec_from_file_location("check_schema", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def check_schema():
    return _module()


def test_the_sequence_check_is_a_no_op_off_postgresql(check_schema, app):
    """SQLite has no sequences; the check must stay silent rather than guess."""
    from app.extensions import db

    with app.app_context():
        assert db.engine.dialect.name == "sqlite", "the suite must not touch PostgreSQL"
        assert check_schema.check_sequences(db, {"game"}) == []


class _PostgresLike:
    """Enough of flask_sqlalchemy's db for check_sequences, answering as PostgreSQL would for tables
    whose one row is id 1 and whose sequence has never been used (last_value 1, is_called false)."""

    def __init__(self, metadata):
        from types import SimpleNamespace
        self.engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        self.metadata = metadata
        self.session = self

    def execute(self, stmt, params=None):
        from types import SimpleNamespace
        sql = str(stmt)
        if "pg_get_serial_sequence" in sql:
            return SimpleNamespace(scalar=lambda: f"public.{params['t']}_id_seq")
        if sql.startswith("SELECT max("):
            return SimpleNamespace(scalar=lambda: 1)
        return SimpleNamespace(one=lambda: (1, False))


def test_a_single_row_table_written_with_its_own_id_is_not_a_stale_sequence(check_schema):
    """2026-11-22: library_state (always id 1, written so) stopped a deploy after its first pull.
    A table off that list in the same state is still caught: the check is not weakened."""
    from app.extensions import db

    fake = _PostgresLike(db.metadata)
    assert "library_state" in check_schema.SINGLE_ROW
    assert check_schema.check_sequences(fake, {"library_state"}) == []
    assert check_schema.check_sequences(fake, {"game"}) == [("game", "public.game_id_seq", "id", 1, 1)]


def test_the_single_row_tables_are_the_ones_written_with_a_fixed_id():
    """Each table on the list is one the code creates with an explicit id; a new entry needs that too."""
    with open(os.path.join(REPO_ROOT, "app", "services", "library_pull.py"), encoding="utf-8") as fh:
        assert "LibraryState(id=1," in fh.read()


def test_the_repair_names_the_table_column_and_sequence(check_schema, capsys):
    check_schema.report_sequences([("game", "public.game_id_seq", "id", 1, 100)])
    out = capsys.readouterr().out
    assert "game.id: next value would be 1, but 100 is already in use" in out
    assert "SELECT setval('public.game_id_seq', (SELECT max(id) FROM \"game\"));" in out


def test_a_non_id_primary_key_is_named_correctly(check_schema, capsys):
    """The repair used to hard-code max(id), which is wrong for any other key."""
    check_schema.report_sequences([("widget", "public.widget_pk_seq", "widget_id", 3, 9)])
    out = capsys.readouterr().out
    assert "max(widget_id)" in out
    assert "max(id)" not in out


# --- the encoding check -----------------------------------------------------

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    """Answers SHOW server_encoding / SHOW client_encoding and nothing else."""

    def __init__(self, server, client):
        self._answers = {"server_encoding": server, "client_encoding": client}

    def execute(self, clause, *args, **kwargs):
        text = str(clause)
        for key, value in self._answers.items():
            if key in text:
                return _FakeResult(value)
        raise AssertionError(f"unexpected query: {text}")


class _FakeDB:
    def __init__(self, dialect, server=None, client=None):
        self.engine = type("E", (), {"dialect": type("D", (), {"name": dialect})()})()
        self.session = _FakeSession(server, client)


def test_encoding_is_not_checked_off_postgresql(check_schema):
    assert check_schema.check_encoding(_FakeDB("sqlite")) is None


def test_a_utf8_database_is_silent(check_schema):
    assert check_schema.check_encoding(_FakeDB("postgresql", "UTF8", "UTF8")) is None


def test_sql_ascii_both_ends_is_fatal_and_names_the_unblock(check_schema):
    """The app cannot store an em dash at all, so the deploy must not proceed."""
    problem = check_schema.check_encoding(_FakeDB("postgresql", "SQL_ASCII", "SQL_ASCII"))
    assert problem is not None and problem["fatal"] is True
    assert "CANNOT STORE NON-ASCII" in problem["message"]
    assert "ALTER ROLE" in problem["message"]


def test_the_unblock_does_not_send_anyone_to_edit_the_credential(check_schema):
    """The first version of this advice said to append client_encoding to
    DATABASE_URL. Doing that means hand-editing the line holding the database
    password; on 2026-10-01 that truncated it and took the site down with
    "password authentication failed", with the old value unrecoverable. The role
    default has the same effect and never touches the file."""
    problem = check_schema.check_encoding(_FakeDB("postgresql", "SQL_ASCII", "SQL_ASCII"))
    assert "DATABASE_URL" not in problem["message"]
    assert ".env" not in problem["message"]


def test_an_overridden_client_warns_but_does_not_block(check_schema):
    """Writes work, so blocking a deploy would be wrong -- but say it is still wrong."""
    problem = check_schema.check_encoding(_FakeDB("postgresql", "SQL_ASCII", "UTF8"))
    assert problem is not None and problem["fatal"] is False
    assert "warning" in problem["message"]
    assert "CANNOT STORE" not in problem["message"]


def test_the_case_of_the_encoding_name_does_not_matter(check_schema):
    assert check_schema.check_encoding(_FakeDB("postgresql", "sql_ascii", "sql_ascii"))["fatal"]
    assert check_schema.check_encoding(_FakeDB("postgresql", "utf8", "utf8")) is None
