#!/usr/bin/env python3
"""Compare the live database's schema against the models, and say where Alembic
should be stamped.

Written after `flask db stamp head` was run against a database whose schema was
*older* than head. Stamping marks migrations as applied without running them, so
`game.barcode` was never created and every page touching a Game raised
`UndefinedColumn`. Checking that all the expected tables existed was not enough:
the tables were all there, and a column was not.

It also checks the database's encoding. A SQL_ASCII database accepts no character
outside ASCII at all: psycopg2 maps the connection to Python's "ascii" codec and an
em dash in a machine note raises UnicodeEncodeError, which the app reports as a
failed save. Same shape of fault again -- everything present, not usable.

It also checks the identity sequences on PostgreSQL. A sequence left behind its
table's maximum id -- which is what happens when rows are restored with explicit
ids and the reset is missed -- lets every read work and makes the next insert fail
with a duplicate-key error. Same shape of bug as the stamp: all present, not usable.

    python scripts/check_schema.py            # report
    python scripts/check_schema.py --quiet    # exit 1 if anything is missing

Never `stamp head` on a database you have not checked. Stamp the revision whose
changes are actually present, then `db upgrade` and let Alembic do the work.
"""
from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Which migration introduced each column, so a gap points at a revision.
# Keep in step with migrations/versions/.
INTRODUCED_BY = {
    ("maintenance_record", "priority"): "ebead5244def",
    ("inventory_request", "maintenance_id"): "3e5463d29981",
    ("inventory_request", "tracking_number"): "e7582856b8aa",
    ("inventory_request", "vendor"): "e7582856b8aa",
    ("inventory_request", "estimated_arrival"): "e7582856b8aa",
    ("inventory_request", "carrier"): "8dcea35845db",
    ("inventory_request", "tracking_status"): "8dcea35845db",
    ("inventory_request", "tracking_details"): "8dcea35845db",
    ("inventory_request", "last_tracking_update"): "8dcea35845db",
    ("maintenance_record", "work_order_type"): "b547f37c117c",
    ("maintenance_record", "location_description"): "b547f37c117c",
    ("game", "last_ranking_update"): "7a61de1d1679",
    ("game", "barcode"): "c1a2b3d4e5f6",
    ("maintenance_record", "external_id"): "d4e5f6a7b8c9",
    ("maintenance_record", "source"): "d4e5f6a7b8c9",
    ("maintenance_record", "rail_session_id"): "d4e5f6a7b8c9",
    ("game", "report_token"): "e6f7a8b9c0d1",
    ("game", "report_label_stale"): "e6f7a8b9c0d1",
    ("maintenance_record", "machine_down"): "e3f4a5b6c7d8",
    ("game_event", "maintenance_id"): "e3f4a5b6c7d8",
    ("maintenance_record", "pm_task_id"): "f4a5b6c7d8e9",
    ("maintenance_record", "reported_by"): "b6c7d8e9f0a1",
    ("maintenance_record", "reported_by_id"): "b6c7d8e9f0a1",
    ("maintenance_record", "fixed_by_id"): "b6c7d8e9f0a1",
    ("device", "all_machines"): "c8d9e0f1a2b3",
    ("inventory_item", "code"): "d9e0f1a2b3c4",
    ("stock_history", "place_id"): "d9e0f1a2b3c4",
    ("stock_history", "to_place_id"): "d9e0f1a2b3c4",
    ("inventory_item", "category_id"): "e0f1a2b3c4d5",
    ("inventory_item", "unit"): "e0f1a2b3c4d5",
    ("inventory_item", "pack_size"): "e0f1a2b3c4d5",
    ("inventory_item", "photo_filename"): "e0f1a2b3c4d5",
    ("inventory_item", "reorder_to"): "f1a2b3c4d5e6",
    ("inventory_request", "quantity_received"): "f1a2b3c4d5e6",
    ("inventory_item", "serialized"): "a2b3c4d5e6f7",
}

# Which migration created each table. Only tables a migration actually creates belong here:
# the older ones (game, user, maintenance_record and the rest) were built by db.create_all()
# before this project had migrations, which is also why `upgrade` from an empty database does
# not work and never has.
#
# Without this a missing table was the one case the report had nothing useful to say about --
# "`db upgrade` may be enough" -- while a missing column got a revision and a stamp command.
TABLE_INTRODUCED_BY = {
    "device": "d4e5f6a7b8c9",
    "rail_session": "d4e5f6a7b8c9",
    "reading": "d4e5f6a7b8c9",
    "rail_session_tag": "b1c2d3e4f5a6",
    "game_event": "d1e2f3a4b5c6",
    "maintenance_task": "f4a5b6c7d8e9",
    "alert_channel": "a5b6c7d8e9f0",
    "alert": "a5b6c7d8e9f0",
    "app_setting": "a5b6c7d8e9f0",
    "device_machine": "c8d9e0f1a2b3",
    "stock_place": "d9e0f1a2b3c4",
    "stock_level": "d9e0f1a2b3c4",
    "part_category": "e0f1a2b3c4d5",
    "supplier": "e0f1a2b3c4d5",
    "item_supplier": "e0f1a2b3c4d5",
    "price_change": "e0f1a2b3c4d5",
    "purchase_order": "f1a2b3c4d5e6",
    "purchase_order_line": "f1a2b3c4d5e6",
    "serial_unit": "a2b3c4d5e6f7",
    "unit_event": "a2b3c4d5e6f7",
    "stock_count": "b3c4d5e6f7a8",
    "stock_count_line": "b3c4d5e6f7a8",
    "part_reservation": "c4d5e6f7a8b9",
    "task_part": "c4d5e6f7a8b9",
    "rom_file": "e5f6a7b8c9d0",
    "rom_dump": "e5f6a7b8c9d0",
    "rom_burn": "e5f6a7b8c9d0",
}

# The revision that precedes each, i.e. what to stamp so `upgrade` will apply it.
PREDECESSOR = {
    "ebead5244def": "5a026e6869ec",
    "3e5463d29981": "ebead5244def",
    "e7582856b8aa": "3e5463d29981",
    "8dcea35845db": "e7582856b8aa",
    "b547f37c117c": "8dcea35845db",
    "7a61de1d1679": "b547f37c117c",
    "c1a2b3d4e5f6": "7a61de1d1679",
    "f1c2d3e4a5b6": "c1a2b3d4e5f6",
    "d4e5f6a7b8c9": "f1c2d3e4a5b6",
    "e6f7a8b9c0d1": "d4e5f6a7b8c9",
    "b1c2d3e4f5a6": "e6f7a8b9c0d1",
    "c7d8e9f0a1b2": "b1c2d3e4f5a6",
    "d1e2f3a4b5c6": "c7d8e9f0a1b2",
    "e3f4a5b6c7d8": "d1e2f3a4b5c6",
    "f4a5b6c7d8e9": "e3f4a5b6c7d8",
    "a5b6c7d8e9f0": "f4a5b6c7d8e9",
    "b6c7d8e9f0a1": "a5b6c7d8e9f0",
    "c8d9e0f1a2b3": "b6c7d8e9f0a1",
    "d9e0f1a2b3c4": "c8d9e0f1a2b3",
    "e0f1a2b3c4d5": "d9e0f1a2b3c4",
    "f1a2b3c4d5e6": "e0f1a2b3c4d5",
    "a2b3c4d5e6f7": "f1a2b3c4d5e6",
    "b3c4d5e6f7a8": "a2b3c4d5e6f7",
    "c4d5e6f7a8b9": "b3c4d5e6f7a8",
    "e5f6a7b8c9d0": "c4d5e6f7a8b9",
}

# Every index app/models/indexes.py declares came from this revision.
INDEXES_INTRODUCED_BY = "c7d8e9f0a1b2"

ORDER = ["5a026e6869ec", "ebead5244def", "3e5463d29981", "e7582856b8aa",
         "8dcea35845db", "b547f37c117c", "7a61de1d1679", "c1a2b3d4e5f6",
         "f1c2d3e4a5b6", "d4e5f6a7b8c9", "e6f7a8b9c0d1", "b1c2d3e4f5a6",
         "c7d8e9f0a1b2", "d1e2f3a4b5c6", "e3f4a5b6c7d8",
         "f4a5b6c7d8e9", "a5b6c7d8e9f0",
         "b6c7d8e9f0a1", "c8d9e0f1a2b3", "d9e0f1a2b3c4",
         "e0f1a2b3c4d5", "f1a2b3c4d5e6",
         "a2b3c4d5e6f7", "b3c4d5e6f7a8",
         "c4d5e6f7a8b9", "e5f6a7b8c9d0"]


def check_encoding(db) -> dict | None:
    """Whether this database can store text the app will be given.

    SQL_ASCII means "no encoding conversion and no validation". With the connection
    also at SQL_ASCII, psycopg2 encodes parameters with Python's "ascii" codec, so a
    single em dash in a machine note raises UnicodeEncodeError and the save fails.
    Forcing client_encoding=utf8 makes writes work -- the server stores the bytes
    verbatim -- but leaves a database with no validation and byte-order collation,
    so it is reported rather than accepted silently.

    Returns None when all is well, else {"fatal": bool, "message": str}.
    """
    if db.engine.dialect.name != "postgresql":
        return None
    server = db.session.execute(sa.text("SHOW server_encoding")).scalar()
    client = db.session.execute(sa.text("SHOW client_encoding")).scalar()
    if (server or "").upper() != "SQL_ASCII":
        return None
    if (client or "").upper() == "SQL_ASCII":
        return {"fatal": True, "message": (
            "\nTHIS DATABASE CANNOT STORE NON-ASCII TEXT\n"
            "  server_encoding and client_encoding are both SQL_ASCII, so psycopg2\n"
            "  encodes with the 'ascii' codec. One em dash in a note raises\n"
            "  UnicodeEncodeError and the save fails.\n"
            "\nTo unblock immediately, as the postgres superuser on the database host:\n"
            "  ALTER ROLE <app role> SET client_encoding TO 'UTF8';\n"
            "  then restart the service. Every new connection by that role gets UTF8.\n"
            "  Reversible with RESET client_encoding, and it does not touch the file\n"
            "  holding the credential -- editing that by hand is how a password gets\n"
            "  truncated and the site starts answering 500.\n"
            "\nThe real fix is to convert the database to UTF8 -- see deploy/RUNBOOK.md,\n"
            "'If the database is SQL_ASCII'.")}
    return {"fatal": False, "message": (
        f"\nwarning: server_encoding is SQL_ASCII (client_encoding is {client}).\n"
        f"  Writes work because the client is overridden, but the database does no\n"
        f"  validation and sorts by byte value. Convert it to UTF8 when convenient --\n"
        f"  see deploy/RUNBOOK.md, 'If the database is SQL_ASCII'.")}


def check_sequences(db, live_tables) -> list[tuple[str, str, str, int, int]]:
    """Integer primary keys whose sequence is at or below the largest id in use.

    PostgreSQL only. Returns (table, sequence, column, next_id, max_id) for each one
    that would hand out a key already in use.
    """
    if db.engine.dialect.name != "postgresql":
        return []
    stale = []
    for name, table in sorted(db.metadata.tables.items()):
        if name not in live_tables:
            continue
        pks = [c for c in table.primary_key.columns]
        if len(pks) != 1 or not isinstance(pks[0].type, sa.Integer):
            continue
        column = pks[0].name
        seq = db.session.execute(
            sa.text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": name, "c": column}
        ).scalar()
        if not seq:
            continue                                  # no sequence: ids come from elsewhere
        biggest = db.session.execute(
            sa.text(f'SELECT max({column}) FROM "{name}"')).scalar()
        if biggest is None:
            continue                                  # empty table, nothing to outrun
        last, called = db.session.execute(
            sa.text("SELECT last_value, is_called FROM " + seq)).one()
        # is_called=false means last_value has not been handed out yet.
        next_id = last + 1 if called else last
        if next_id <= biggest:
            stale.append((name, seq, column, next_id, biggest))
    return stale


def report_sequences(stale: list[tuple[str, str, str, int, int]]) -> None:
    print("\nIDENTITY SEQUENCES ARE BEHIND THEIR TABLES")
    print("Reads work; the next insert fails with a duplicate key.")
    for name, _seq, column, next_id, biggest in stale:
        print(f"  {name}.{column}: next value would be {next_id}, "
              f"but {biggest} is already in use")
    print("\nTo repair, move each sequence past the rows that exist:")
    for name, seq, column, _next_id, _biggest in stale:
        print(f'  SELECT setval(\'{seq}\', (SELECT max({column}) FROM "{name}"));')


def check_indexes(inspector, live_tables) -> list[str]:
    """Indexes the models declare that the database lacks.

    A warning, not a failure: without them every page still works, just slower as history
    grows. It shows up when a database was stamped past c7d8e9f0a1b2 without running it.
    """
    from app.models.indexes import INDEXES

    live = {}
    for table in {t for t, *_ in INDEXES.values()} & live_tables:
        live[table] = {i["name"] for i in inspector.get_indexes(table)}
    return [name for name, (table, *_c) in INDEXES.items()
            if table in live and name not in live[table]]


def report_indexes(missing: list[str]) -> None:
    print(f"\nWARNING: {len(missing)} index(es) missing (pages work, but slow as history grows):")
    for name in missing:
        print(f"  missing index : {name}")
    print("The migration that adds them is idempotent; to run it again:")
    print(f"  flask --app run:app db stamp {PREDECESSOR[INDEXES_INTRODUCED_BY]}")
    print("  flask --app run:app db upgrade")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--quiet", action="store_true", help="only report problems")
    args = p.parse_args()

    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        uri = app.config["SQLALCHEMY_DATABASE_URI"]
        print("database:", uri.split("@")[-1] if "@" in uri else uri)
        inspector = sa.inspect(db.engine)
        live_tables = set(inspector.get_table_names())

        missing_tables = sorted(set(db.metadata.tables) - live_tables)
        missing_columns: list[tuple[str, str]] = []
        for name, table in db.metadata.tables.items():
            if name not in live_tables:
                continue
            live = {c["name"] for c in inspector.get_columns(name)}
            for column in table.columns:
                if column.name not in live:
                    missing_columns.append((name, column.name))

        encoding_problem = check_encoding(db)
        stale_sequences = check_sequences(db, live_tables)
        missing_indexes = check_indexes(inspector, live_tables)

        stamped = None
        if "alembic_version" in live_tables:
            stamped = db.session.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar()
        print("alembic  :", stamped or "NOT STAMPED (no alembic_version row)")

        if encoding_problem:
            print(encoding_problem["message"])
        if missing_indexes:
            report_indexes(missing_indexes)

        if (not missing_tables and not missing_columns and not stale_sequences
                and not (encoding_problem or {}).get("fatal")):
            if not args.quiet:
                print("schema   : matches the models")
                if stamped != ORDER[-1]:
                    print(f"\nNote: the schema is complete but stamped at {stamped!r},"
                          f" not head ({ORDER[-1]}).")
                    print("`db upgrade` may try to re-apply changes that already exist.")
            return 0

        if not missing_tables and not missing_columns:
            if stale_sequences:
                report_sequences(stale_sequences)
            return 1

        print("\nSCHEMA IS BEHIND THE MODELS")
        for name in missing_tables:
            rev = TABLE_INTRODUCED_BY.get(name, "?")
            print(f"  missing table : {name}   (added by {rev})")
        for table, column in missing_columns:
            rev = INTRODUCED_BY.get((table, column), "?")
            print(f"  missing column: {table}.{column}   (added by {rev})")

        revs = {INTRODUCED_BY[k] for k in missing_columns if k in INTRODUCED_BY}
        revs |= {TABLE_INTRODUCED_BY[t] for t in missing_tables
                 if t in TABLE_INTRODUCED_BY}
        if revs:
            earliest = min(revs, key=ORDER.index)
            target = PREDECESSOR.get(earliest)
            print(f"\nEarliest missing change comes from {earliest}.")
            print("To repair, stamp the revision before it and let Alembic run the rest:")
            print(f"  flask --app run:app db stamp {target}")
            print("  flask --app run:app db upgrade")
        else:
            # Only reachable for a table no migration creates -- one of the create_all-era
            # tables, which means the database was never built from this repo at all.
            print("\nMissing tables that no migration creates; this database was not built"
                  " from this repo. `flask --app run:app db upgrade` will not add them.")
        if stale_sequences:
            report_sequences(stale_sequences)
        return 1


if __name__ == "__main__":
    sys.exit(main())
