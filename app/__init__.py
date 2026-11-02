"""Cabcrawl application package.

Provides the :func:`create_app` factory used to build and configure the
Flask application instance.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from flask import Flask, Response, request

from app.extensions import csrf, db, limiter, login_manager, migrate

if TYPE_CHECKING:
    from app.config import Settings

def create_app() -> Flask:
    """Create and configure the Flask application.

    * Loads environment from ``.env``
    * Configures database, uploads, sessions, and security
    * Initialises extensions and registers blueprints
    * Adds security headers via ``after_request``

    Returns:
        A fully-configured :class:`~flask.Flask` instance.
    """
    load_dotenv()

    app = Flask(
        __name__,
        instance_relative_config=False,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )

    settings = _configure_app(app)
    from app.logs import configure as configure_logging

    configure_logging(app)
    for warning in settings.warnings:
        app.logger.warning("%s", warning, extra={"fields": {"event": "CONFIG_WARNING"}})
    _create_directories(app)
    _init_extensions(app)
    from app.services.alert_events import install as install_alerts
    from app.services.history import install as install_history

    install_history()
    install_alerts()
    _register_blueprints(app)
    _guard_static_photos(app)
    _register_context_processors(app)
    _register_error_handlers(app)
    _register_after_request(app)

    from app.security.https import HttpsAware

    app.wsgi_app = HttpsAware(app.wsgi_app)
    return app


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _configure_app(app: Flask) -> Settings:
    """Apply all configuration values to *app*. Environment settings are read and checked in
    app/config.py (Step 5.6), which raises ConfigError listing every problem at once."""
    from app.config import load

    base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(base_dir, "instance", "arcade.db")
    settings = load(default_db=f"sqlite:///{db_path}")
    settings.apply(app.config)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Uploads ------------------------------------------------------
    app.config["UPLOAD_FOLDER"] = os.path.join(base_dir, "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

    # CSRF ---------------------------------------------------------
    # Enforced on every POST (Flask-WTF's default; it was switched off until 2026-10-08).
    # The device API is exempt in app/routes/api_v1.py. Tokens live as long as the session
    # rather than the default hour, so a form opened at the start of a shift still submits.
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # Session / Remember-me ----------------------------------------
    import datetime as dt

    app.config["REMEMBER_COOKIE_DURATION"] = dt.timedelta(days=30)
    app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(days=30)
    # Secure is added per request by app.security.https.HttpsAware, so the cookies are
    # Secure through the tunnel and still work over plain HTTP on the LAN (F-22).
    app.config["REMEMBER_COOKIE_SECURE"] = False
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_REFRESH_EACH_REQUEST"] = False
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    return settings


def _create_directories(app: Flask) -> None:
    """Ensure all required directories exist.

    Work-order photos are saved in uploads/maintenance_photos/. static/maintenance_photos/
    is where they were saved until 2026-10-28; it is still read until its files are moved,
    and it stays in this list because the systemd unit names it in ReadWritePaths, which
    refuses to start the service if it is missing.
    """
    base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    directories = [
        os.path.join(base_dir, "instance"),
        app.config["UPLOAD_FOLDER"],
        os.path.join(app.config["UPLOAD_FOLDER"], "maintenance_photos"),
        os.path.join(base_dir, "backups"),
        os.path.join(base_dir, "logs"),
        os.path.join(base_dir, "static", "maintenance_photos"),
        os.path.join(base_dir, "static", "profile_pics"),
    ]
    for directory in directories:
        os.makedirs(directory, exist_ok=True)


def _init_extensions(app: Flask) -> None:
    """Bind all Flask extensions to *app*."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Import models so that SQLAlchemy is aware of them
    from app import models as _models  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id: str):  # noqa: ANN202
        from app.models import User

        # A deactivated account is signed out on its next request. UserMixin.is_authenticated
        # already says so; refusing it here too keeps that true if User ever overrides it.
        user = db.session.get(User, int(user_id))
        return user if user is not None and user.is_active else None


def _register_blueprints(app: Flask) -> None:
    """Import and register all application blueprints."""
    blueprint_imports: list[tuple[str, str]] = [
        ("app.routes.auth", "auth_bp"),
        ("app.routes.dashboard", "dashboard_bp"),
        ("app.routes.games", "games_bp"),
        ("app.routes.collection", "collection_bp"),
        ("app.routes.rotation", "rotation_bp"),
        ("app.routes.upkeep", "upkeep_bp"),
        ("app.routes.alerts", "alerts_bp"),
        ("app.routes.branding", "branding_bp"),
        ("app.routes.devices", "devices_bp"),
        ("app.routes.maintenance", "maintenance_bp"),
        ("app.routes.inventory", "inventory_bp"),
        ("app.routes.places", "places_bp"),
        ("app.routes.stock_labels", "stock_labels_bp"),
        ("app.routes.parts", "parts_bp"),
        ("app.routes.purchasing", "purchasing_bp"),
        ("app.routes.serials", "serials_bp"),
        ("app.routes.counts", "counts_bp"),
        ("app.routes.reservations", "reservations_bp"),
        ("app.routes.reports", "reports_bp"),
        ("app.routes.admin", "admin_bp"),
        ("app.routes.api_v1", "api_v1_bp"),
        ("app.routes.rails", "rails_bp"),
        ("app.routes.report", "report_bp"),
        ("app.routes.styleguide", "styleguide_bp"),
    ]

    import importlib

    for module_path, bp_name in blueprint_imports:
        try:
            mod = importlib.import_module(module_path)
            bp = getattr(mod, bp_name, None)
            if bp is not None:
                app.register_blueprint(bp)
        except ImportError as exc:
            app.logger.warning("blueprint not loaded", extra={"fields": {
                "event": "BLUEPRINT_MISSING", "module": module_path, "error": str(exc)}})


def _guard_static_photos(app: Flask) -> None:
    """Never let the public static route serve a work-order photo.

    Photos were saved in static/maintenance_photos/ until 2026-10-28, and files may still be
    there until scripts/move_maintenance_photos.py has run. ``/static/maintenance_photos/<f>``
    itself is a rule of its own that redirects to the login-protected address
    (maintenance.legacy_photo), but the static route resolves other spellings of the same
    folder too (``/static/css/../maintenance_photos/<f>``, ``/static/./maintenance_photos/<f>``).
    Whatever reaches the static view and lands in that folder gets the same redirect, or a
    404 for a name that isn't a photo's.
    """
    import posixpath

    from flask import abort, redirect, url_for

    from app.services.storage import PHOTO_NAME, PHOTO_SUBDIR

    serve_static = app.view_functions["static"]

    def static(filename: str):
        parts = posixpath.normpath("/" + filename.replace("\\", "/")).lstrip("/").split("/")
        if parts and parts[0].lower() == PHOTO_SUBDIR:
            name = "/".join(parts[1:])
            if PHOTO_NAME.match(name):
                return redirect(url_for("maintenance.photo_file", filename=name), 302)
            abort(404)
        return serve_static(filename=filename)

    app.view_functions["static"] = static


def _register_context_processors(app: Flask) -> None:
    """Register Jinja2 context processors for global template helpers."""
    from datetime import date as _date

    from flask import url_for as _url_for

    from app.ui import register as register_ui_filters

    register_ui_filters(app)

    @app.context_processor
    def branding_processor() -> dict:
        """The arcade's name and logo, for every page (app/services/branding.py)."""
        from app.services.branding import current

        try:
            return {"branding": current()}
        except Exception:  # noqa: BLE001 -- a missing table must not take every page down
            return {"branding": {"name": None, "logo_url": None, "logo_is_svg": False,
                                 "background_url": None, "print_logo_url": None}}

    @app.context_processor
    def utility_processor() -> dict:
        def get_cloud_url(filename: str) -> str:
            """A maintenance photo's URL: always this server's copy, behind the login
            (maintenance.photo_file). The S3 copy is a backup; serving from it would also
            break the Content-Security-Policy (img-src 'self'). This always returned the
            local URL in practice, because it read a config key that was never set (Step 5.6
            made that explicit)."""
            return _url_for("maintenance.photo_file", filename=filename)

        return {"today": _date.today, "get_cloud_url": get_cloud_url}


# Everything this application loads is served from this application. There is one
# Content-Security-Policy in this codebase and it is the one below, sent from the
# after_request that actually runs.
#
# No 'unsafe-inline' (Step 5.3): no page has an inline script, handler or style any more.
# Page data reaches scripts as <script type="application/json"> islands, which are data, not
# script, so the policy doesn't apply to them. tests/test_csp.py keeps it that way.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "          # data: for the QR labels, which are inline SVG
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)


ERROR_PAGES = {
    403: ("Not allowed", "Your account cannot open this page. If you need it, ask an admin "
          "to change your role.", "warn"),
    429: ("Too many attempts", "Too many tries from this network in a short time. Wait a "
          "minute and try again; if you've forgotten your password, ask an admin to reset it.",
          "warn"),
    404: ("Nothing here", "That page or record does not exist. It may have been deleted, or the "
          "link is mistyped. Machine labels still work: scan the code again.", "info"),
    500: ("Something broke", "The tracker hit an error and nothing was saved. Try again; if it "
          "keeps happening, tell an admin what you were doing.", "fault"),
}


def _register_error_handlers(app: Flask) -> None:
    from flask import jsonify, render_template
    from flask_wtf.csrf import CSRFError

    def page(code: int):
        title, text, tone = ERROR_PAGES[code]
        # Machine clients get JSON: /api/v1 is read by GATBOX, not a browser.
        if request.path.startswith("/api/"):
            return jsonify({"error": title.lower()}), code
        try:
            return render_template("errors/error.html", code=code, title=title, text=text,
                                   tone=tone), code
        except Exception:  # noqa: BLE001 -- the error page must not raise its own error
            return f"{code} {title}", code

    for code in ERROR_PAGES:
        app.register_error_handler(code, lambda error, code=code: page(code))

    @app.errorhandler(CSRFError)
    def csrf_error(error):  # noqa: ANN202
        app.logger.warning("CSRF refused: %s %s (%s)", request.method, request.path,
                           error.description)
        return render_template("errors/csrf.html"), 400


def _register_after_request(app: Flask) -> None:
    """Add security headers to every response."""

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Phase 2.5 put a credential in a URL: /report/<token>, the coin-door form. Any
        # off-origin request a page makes would carry that URL in its Referer, so no other
        # site is ever sent one. Set for the whole application rather than on the one
        # blueprint that needs it, because a header only some responses carry is a header
        # someone will forget.
        #
        # same-origin, not no-referrer: over HTTPS Flask-WTF refuses a form posted without a
        # Referer from this host (WTF_CSRF_SSL_STRICT). With no-referrer, once Step 5.1 let
        # the app see the tunnel's HTTPS, every form posted through it was refused, the
        # login included (fixed 2026-10-26).
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # A response that sets a stricter policy of its own keeps it: an SVG logo is served
        # with sandbox (app/routes/branding.py), which this would otherwise overwrite.
        response.headers.setdefault("Content-Security-Policy", CSP)
        return response
