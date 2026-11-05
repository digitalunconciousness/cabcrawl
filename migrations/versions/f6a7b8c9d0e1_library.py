"""The library's read-only copy: ``game.model_key``, ``library_record`` and ``library_state``.

``game.model_key`` is the game a machine is in GATBOX's library (nullable; the contract's roster
`model_key`). ``library_record`` holds the archive's records for the games the floor's machines name,
one row per record with its data as JSON; ``library_state`` is the pull job's cursor and how its last
run went. No data moves, and nothing is written until scripts/link_model_keys.py and the pull job run.
Guarded like its neighbours, so a re-run is harmless.

Downgrade drops the two tables and the column: the copy and the links are gone, and the next pull
job starts from nothing. The archive is untouched. The PDFs stay on disk, unreferenced.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade():
    if "model_key" not in {c["name"] for c in _inspector().get_columns("game")}:
        with op.batch_alter_table("game") as batch:
            batch.add_column(sa.Column("model_key", sa.String(length=60), nullable=True))
    if not _inspector().has_table("library_record"):
        op.create_table(
            "library_record",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("rid", sa.String(length=200), nullable=False),
            sa.Column("model_key", sa.String(length=60), nullable=True),
            sa.Column("rev", sa.Integer(), nullable=False),
            sa.Column("hash", sa.String(length=64), nullable=False),
            sa.Column("at", sa.Float(), nullable=True),
            sa.Column("by", sa.String(length=60), nullable=True),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("fetched", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("kind", "rid", name="uq_library_record_kind_rid"),
        )
        op.create_index("ix_library_record_model_key", "library_record", ["model_key"])
    if not _inspector().has_table("library_state"):
        op.create_table(
            "library_state",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cursor", sa.BigInteger(), nullable=False),
            sa.Column("keys_synced", sa.JSON(), nullable=False),
            sa.Column("last_try", sa.DateTime(), nullable=True),
            sa.Column("last_ok", sa.DateTime(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("refused", sa.Boolean(), nullable=False),
            sa.Column("records", sa.Integer(), nullable=False),
        )


def downgrade():
    if _inspector().has_table("library_state"):
        op.drop_table("library_state")
    if _inspector().has_table("library_record"):
        op.drop_index("ix_library_record_model_key", table_name="library_record")
        op.drop_table("library_record")
    if "model_key" in {c["name"] for c in _inspector().get_columns("game")}:
        with op.batch_alter_table("game") as batch:
            batch.drop_column("model_key")
