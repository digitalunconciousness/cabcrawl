"""Game and play-record models."""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from app.extensions import db


class Game(db.Model):
    """An arcade game tracked by the system."""

    __tablename__ = "game"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(100), nullable=False)
    barcode: str | None = db.Column(db.String(64), unique=True, nullable=True)
    manufacturer: str | None = db.Column(db.String(50), nullable=True)
    year: int | None = db.Column(db.Integer, nullable=True)
    genre: str | None = db.Column(db.String(50), nullable=True)
    # active_history: load the old value before it's overwritten, so the history hook
    # (app/services/history.py) can record what it was, and skip a "change" to the same value.
    location = db.column_property(db.Column(db.String(20), default="Warehouse"),
                                  active_history=True)
    floor_position: str | None = db.Column(db.String(50), nullable=True)
    warehouse_section: str | None = db.Column(db.String(50), nullable=True)
    status = db.column_property(db.Column(db.String(20), default="Working"),
                                active_history=True)
    coins_per_play: float = db.Column(db.Float, default=0.25)
    total_plays: int = db.Column(db.Integer, default=0)
    total_revenue: float = db.Column(db.Float, default=0.0)
    counter_status: str = db.Column(db.String(20), default="Working")
    counter_notes: str | None = db.Column(db.Text, nullable=True)
    date_added: datetime = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    notes: str | None = db.Column(db.Text, nullable=True)
    image_filename: str | None = db.Column(db.String(255), nullable=True)
    times_in_top_5: int = db.Column(db.Integer, default=0)
    times_in_top_10: int = db.Column(db.Integer, default=0)
    last_ranking_update: date | None = db.Column(db.Date, nullable=True)

    # The game this machine is, in GATBOX's library (contract v1's roster `model_key`; the library's
    # own key, permanent). Nullable: a machine the library doesn't know has no game section. Set by
    # scripts/link_model_keys.py from the seed plan, and editable by managers on the machine page.
    model_key: str | None = db.Column(db.String(60), nullable=True)

    # The secret a coin-door label carries, so a request can be filed with no login by
    # whoever can open the door. **Never barcode.** barcode is the public identifier -- the
    # roster slug, printed on the cabinet labels, shared with GATBOX -- and the slugs are
    # guessable, which on a site published through a Cloudflare tunnel means anyone on the
    # internet could file for any machine. Nullable: a machine with no coin-door label has
    # no token, and minting one is a decision taken when a sheet is printed.
    report_token: str | None = db.Column(db.String(64), unique=True, nullable=True)
    # Set by a rotation: the label already inside the door now points at a dead URL, so the
    # machine has to appear on a reprint list rather than being quietly unreportable.
    report_label_stale: bool = db.Column(db.Boolean, default=False, nullable=False)

    # Relationships
    play_records = db.relationship(
        "PlayRecord", backref="game", lazy=True, cascade="all, delete-orphan"
    )
    maintenance_records = db.relationship(
        "MaintenanceRecord", backref="game", lazy=True, cascade="all, delete-orphan"
    )

    # ------------------------------------------------------------------
    # Coin-door report token
    # ------------------------------------------------------------------
    def mint_report_token(self) -> str:
        """This machine's token, minting one the first time.

        Idempotent on purpose: printing a second sheet must not invalidate the label
        already inside a door. Rotation is the explicit way to replace one.
        """
        if not self.report_token:
            self.report_token = secrets.token_hex(16)
        return self.report_token

    def rotate_report_token(self) -> str:
        """Replace the token, retiring the old one, and flag the label for reprint."""
        self.report_token = secrets.token_hex(16)
        self.report_label_stale = True
        return self.report_token

    @classmethod
    def by_report_token(cls, token: str | None) -> "Game | None":
        """The machine a token belongs to, or None.

        The emptiness guard is not defensive tidying: ``filter_by(report_token=None)``
        matches every machine that has no label yet, so without it an empty token would
        resolve to an arbitrary machine and authorise a write against it.
        """
        if not token:
            return None
        return cls.query.filter_by(report_token=token).first()

    def __repr__(self) -> str:
        return f"<Game {self.name!r}>"


class PlayRecord(db.Model):
    """A single play-count / coin-count snapshot for a game."""

    __tablename__ = "play_record"

    id: int = db.Column(db.Integer, primary_key=True)
    game_id: int = db.Column(
        db.Integer, db.ForeignKey("game.id"), nullable=False
    )
    coin_count: int = db.Column(db.Integer, nullable=False, default=0)
    plays_count: int = db.Column(db.Integer, nullable=False, default=0)
    revenue: float = db.Column(db.Float, nullable=False, default=0.0)
    date_recorded: date = db.Column(db.Date, nullable=False, default=date.today)
    notes: str | None = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<PlayRecord game_id={self.game_id} date={self.date_recorded}>"
