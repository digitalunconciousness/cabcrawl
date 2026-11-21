"""Each user's desktop look: ``user.desk_theme`` (synthwave, ledger, graphite or system).

Not null, server default 'synthwave', so every existing user keeps exactly what they see now. The
look applies at 1024 px and wider; phones always get synthwave (the owner, 2026-11-21, matching the
Tech Tracker). Guarded like its neighbours, so a re-run is harmless.

Downgrade drops the column: everyone's choice goes, and every screen is synthwave again.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _columns():
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}


def upgrade():
    if "desk_theme" not in _columns():
        with op.batch_alter_table("user") as batch:
            batch.add_column(sa.Column("desk_theme", sa.String(length=10), nullable=False,
                                       server_default="synthwave"))


def downgrade():
    if "desk_theme" in _columns():
        with op.batch_alter_table("user") as batch:
            batch.drop_column("desk_theme")
