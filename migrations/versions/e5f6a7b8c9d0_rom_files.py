"""ROM images from GATBOX (contract v1, capability roms): files, dumps and burns.

Adds ``rom_file`` (one row per distinct image, named by its SHA-256; the bytes live in the
file store, not here), ``rom_dump`` (a chip read twice identically) and ``rom_burn`` (an
attempt to write a chip, verified or stopped). No data moves. Guarded like its neighbours,
so a re-run is harmless.

Downgrade drops the three tables and with them every dump and burn record GATBOX sent. The
image files stay on disk, unreferenced.

Revision ID: e5f6a7b8c9d0
Revises: c4d5e6f7a8b9
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade():
    if not _inspector().has_table("rom_file"):
        op.create_table(
            "rom_file",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sha256", sa.String(length=64), nullable=False, unique=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("device_id", sa.Integer(),
                      sa.ForeignKey("device.id", ondelete="SET NULL",
                                    name="fk_rom_file_device_id"), nullable=True),
            sa.Column("created", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_rom_file_device_id", "rom_file", ["device_id"])
    if not _inspector().has_table("rom_dump"):
        op.create_table(
            "rom_dump",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uid", sa.String(length=32), nullable=False, unique=True),
            sa.Column("device_id", sa.Integer(),
                      sa.ForeignKey("device.id", ondelete="CASCADE"), nullable=False),
            sa.Column("game_id", sa.Integer(),
                      sa.ForeignKey("game.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rom_file_id", sa.Integer(),
                      sa.ForeignKey("rom_file.id"), nullable=False),
            sa.Column("maintenance_record_id", sa.Integer(),
                      sa.ForeignKey("maintenance_record.id", ondelete="SET NULL",
                                    name="fk_rom_dump_maintenance_record_id"), nullable=True),
            sa.Column("label", sa.String(length=40), nullable=False),
            sa.Column("part", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("crc32", sa.String(length=8), nullable=False),
            sa.Column("sha1", sa.String(length=40), nullable=False),
            sa.Column("romident", sa.JSON(), nullable=True),
            sa.Column("reads", sa.Integer(), nullable=False),
            sa.Column("identical", sa.Boolean(), nullable=False),
            sa.Column("blank", sa.Boolean(), nullable=False),
            sa.Column("taken", sa.DateTime(), nullable=False),
            sa.Column("clock", sa.String(length=12), nullable=True),
            sa.Column("created", sa.DateTime(), nullable=False),
        )
        for column in ("device_id", "game_id", "rom_file_id", "maintenance_record_id"):
            op.create_index(f"ix_rom_dump_{column}", "rom_dump", [column])
    if not _inspector().has_table("rom_burn"):
        op.create_table(
            "rom_burn",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uid", sa.String(length=32), nullable=False, unique=True),
            sa.Column("device_id", sa.Integer(),
                      sa.ForeignKey("device.id", ondelete="CASCADE"), nullable=False),
            sa.Column("game_id", sa.Integer(),
                      sa.ForeignKey("game.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rom_file_id", sa.Integer(),
                      sa.ForeignKey("rom_file.id"), nullable=False),
            sa.Column("maintenance_record_id", sa.Integer(),
                      sa.ForeignKey("maintenance_record.id", ondelete="SET NULL",
                                    name="fk_rom_burn_maintenance_record_id"), nullable=True),
            sa.Column("image_sha1", sa.String(length=40), nullable=False),
            sa.Column("part", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(length=8), nullable=False),
            sa.Column("vpp", sa.String(length=20), nullable=True),
            sa.Column("blank_ok", sa.Boolean(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("burned", sa.DateTime(), nullable=False),
            sa.Column("created", sa.DateTime(), nullable=False),
        )
        for column in ("device_id", "game_id", "rom_file_id", "maintenance_record_id"):
            op.create_index(f"ix_rom_burn_{column}", "rom_burn", [column])


def downgrade():
    for table in ("rom_burn", "rom_dump", "rom_file"):
        if _inspector().has_table(table):
            op.drop_table(table)
