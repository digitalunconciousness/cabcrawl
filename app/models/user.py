"""User model with role-based access control."""

from __future__ import annotations

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


DESK_THEMES = {"synthwave": "Synthwave", "ledger": "Ledger (light)", "graphite": "Graphite (dark)",
               "system": "Follow the system (Ledger, or Graphite when the device is dark)"}


class User(UserMixin, db.Model):
    """Application user with role-based access control.

    Roles (ascending privilege): readonly → operator → manager → admin.
    """

    __tablename__ = "user"

    id: int = db.Column(db.Integer, primary_key=True)
    username: str = db.Column(db.String(80), unique=True, nullable=False)
    password_hash: str = db.Column(db.String(256), nullable=False)
    role: str = db.Column(db.String(20), nullable=False, default="readonly")
    is_active: bool = db.Column(db.Boolean, default=True)
    created_at: datetime = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    must_change_password: bool = db.Column(db.Boolean, default=True)
    profile_picture: str | None = db.Column(db.String(255), nullable=True)
    last_login: datetime | None = db.Column(db.DateTime, nullable=True)
    # The look on a computer (1024 px and wider): one of DESK_THEMES. Phones always get synthwave (the owner,
    # 2026-11-21, matching the Tech Tracker's "Desktop look"). Changed on Profile.
    desk_theme: str = db.Column(db.String(10), nullable=False, default="synthwave", server_default="synthwave")

    ROLE_HIERARCHY: dict[str, int] = {
        "readonly": 1,
        "operator": 2,
        "manager": 3,
        "admin": 4,
    }

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------
    def set_password(self, password: str) -> None:
        """Hash and store *password*."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Return ``True`` if *password* matches the stored hash."""
        return check_password_hash(self.password_hash, password)

    # ------------------------------------------------------------------
    # Role helpers
    # ------------------------------------------------------------------
    def has_role(self, role: str) -> bool:
        """Return ``True`` if the user's role is >= *role* in the hierarchy.

        An unknown *role* (a typo) grants nothing rather than everything, and so does an
        unknown role on the user.
        """
        required_level = self.ROLE_HIERARCHY.get(role)
        if required_level is None:
            return False
        return self.ROLE_HIERARCHY.get(self.role, 0) >= required_level

    # ------------------------------------------------------------------
    # Flask-Login interface
    # ------------------------------------------------------------------
    def get_id(self) -> str:
        return str(self.id)

    def __repr__(self) -> str:
        return f"<User {self.username!r} role={self.role!r}>"
