"""Authentication blueprint — login, logout, setup, profile, change password."""

import datetime as dt
import os
import uuid
from datetime import datetime

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db, limiter
from app.forms.auth import ChangePasswordForm, LoginForm, ProfileForm, ThemeForm
from app.models import User
from app.security.utils import (
    check_password_strength,
    clear_failed_login_attempts,
    get_client_ip,
    is_account_locked,
    is_safe_redirect_url,
    log_security_event,
    record_failed_login,
)
from app.utils.helpers import allowed_file, compress_and_save_image

auth_bp = Blueprint("auth", __name__)


def _failed_sign_in(response) -> bool:
    """Only failures spend the login budget: a success redirects, a failure re-renders."""
    return response.status_code == 200


# Per visitor address (app/security/https.client_ip). A whole arcade shares one public address,
# so only failed attempts count: a room of staff signing in correctly never reaches it, and
# someone guessing passwords does within a minute. The per-username lockout below still applies.
@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute;50 per hour", methods=["POST"], deduct_when=_failed_sign_in)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()

        # Check if account is locked
        if is_account_locked(username):
            log_security_event(
                "LOGIN_ATTEMPT_LOCKED_ACCOUNT",
                details=f"Username: {username}, IP: {get_client_ip()}",
            )
            flash(
                "Account temporarily locked due to multiple failed login attempts. "
                "Try again in 15 minutes.",
                "error",
            )
            return render_template("login.html", form=form)

        # Case-insensitive username lookup
        user = User.query.filter(User.username.ilike(username)).first()

        if user and user.check_password(form.password.data) and user.is_active:
            # Clear failed login attempts
            clear_failed_login_attempts(username)

            # Update last login
            user.last_login = datetime.now(dt.UTC)
            db.session.commit()

            # Login user with remember me option
            session.permanent = True
            login_user(
                user,
                remember=form.remember.data,
                duration=dt.timedelta(days=30),
            )

            # Log successful login
            log_security_event(
                "LOGIN_SUCCESS",
                user_id=user.id,
                details=f"Username: {username}",
            )

            # Check if user must change password
            if user.must_change_password:
                flash("You must change your password before continuing.", "warning")
                return redirect(url_for("auth.change_password"))

            flash(f"Welcome back, {user.username}!", "success")

            # Validate redirect URL to prevent open redirect
            next_page = request.args.get("next")
            if next_page and not is_safe_redirect_url(next_page):
                next_page = None

            return redirect(next_page) if next_page else redirect(
                url_for("dashboard.home")
            )

        # Record failed login attempt
        is_locked = record_failed_login(username)

        # Log failed login
        log_security_event(
            "LOGIN_FAILED",
            details=f"Username: {username}, IP: {get_client_ip()}",
        )

        if is_locked:
            flash(
                "Too many failed login attempts. Account locked for 15 minutes.",
                "error",
            )
        else:
            flash("Invalid username or password", "error")

    return render_template("login.html", form=form)


# Endpoints a signed-in user who must change their password can still reach.
_PASSWORD_CHANGE_EXEMPT = {"auth.change_password", "auth.logout", "static"}


@auth_bp.before_app_request
def _hold_until_password_changed():
    """A user with a one-time or reset password goes nowhere but the change-password page.

    The login view redirected there once, and any link then skipped it, so a password an
    admin had read off the screen stayed in use indefinitely (F-41). Machines (the API's
    bearer tokens) and the coin-door form have no session, so they are untouched.
    """
    if (current_user.is_authenticated and current_user.must_change_password
            and request.endpoint not in _PASSWORD_CHANGE_EXEMPT):
        return redirect(url_for("auth.change_password"))
    return None


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    """POST signs out. GET only asks, so a link, a prefetch or an image tag on another site
    cannot sign anyone out (F-21)."""
    if request.method == "GET":
        return render_template("logout.html")
    log_security_event(
        "LOGOUT",
        user_id=current_user.id,
        details=f"Username: {current_user.username}",
    )
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-time setup: create the first administrator. Only while no users exist.

    The page used to render a template that needed a form this view never passed, so a
    fresh install got a 500 here (F-36). It also took any password at all; the first
    account is an admin, so it now meets the same strength rule as a password change.
    """
    if User.query.count() > 0:
        flash("Setup has already been completed.", "info")
        return redirect(url_for("auth.login"))

    username, errors = "", {}
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if len(username) < 3:
            errors["username"] = "Use at least 3 characters."
        strong, why = check_password_strength(password)
        if not strong:
            errors["password"] = why
        elif password != confirm:
            errors["confirm"] = "The two passwords are different."

        if not errors:
            user = User(username=username, role="admin", is_active=True,
                        must_change_password=False)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            log_security_event("SETUP_ADMIN_CREATED", user_id=user.id,
                               details=f"Username: {username}", level="warning")
            flash("Administrator created. Sign in to continue.", "success")
            return redirect(url_for("auth.login"))

    return render_template("setup.html", username=username, errors=errors), (
        400 if errors else 200)


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm()
    if form.validate_on_submit():
        if not form.profile_picture.data:
            flash("Choose a photo first.", "error")
            return redirect(url_for("auth.profile"))
        upload = form.profile_picture.data
        if not allowed_file(upload.filename):
            flash("That isn't a photo. Use PNG, JPG or GIF.", "error")
            return redirect(url_for("auth.profile"))

        from flask import current_app

        folder = os.path.join(current_app.static_folder, "profile_pics")
        os.makedirs(folder, exist_ok=True)
        # Re-encoded to JPEG, so named .jpg (F-8: it kept the upload's extension).
        name = f"profile_{current_user.id}_{uuid.uuid4().hex[:8]}.jpg"
        path = os.path.join(folder, name)
        if not compress_and_save_image(upload, path):
            # F-8: an unreadable file used to become the profile picture anyway.
            if os.path.exists(path):
                os.remove(path)
            flash(f"{upload.filename} could not be read as an image.", "error")
            return redirect(url_for("auth.profile"))

        old = current_user.profile_picture
        current_user.profile_picture = name
        db.session.commit()
        if old and os.path.basename(old) == old:
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
        flash("Photo updated.", "success")
        return redirect(url_for("auth.profile"))

    from app.services.accounts import ROLE_TEXT

    return render_template("profile.html", form=form, role_text=ROLE_TEXT,
                           theme_form=ThemeForm(desk_theme=current_user.desk_theme)), (
        400 if form.errors else 200)


@auth_bp.route("/profile/theme", methods=["POST"])
@login_required
def profile_theme():
    """The desktop look, per person (the owner, 2026-11-21). Phones always get synthwave."""
    form = ThemeForm()
    if form.validate_on_submit():
        current_user.desk_theme = form.desk_theme.data
        db.session.commit()
        flash("Desktop look saved.", "success")
    else:
        flash("Pick one of the looks.", "error")
    return redirect(url_for("auth.profile") + "#look")


@auth_bp.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("That isn't your current password.")
        elif form.new_password.data == form.current_password.data:
            form.new_password.errors.append("Choose a password different from the current one.")
        else:
            strong, why = check_password_strength(form.new_password.data)
            if not strong:
                form.new_password.errors.append(why + ".")
            else:
                was_held = current_user.must_change_password
                current_user.set_password(form.new_password.data)
                current_user.must_change_password = False
                db.session.commit()
                log_security_event("PASSWORD_CHANGED", user_id=current_user.id,
                                   details=f"Username: {current_user.username}")
                flash("Password changed.", "success")
                # Out of the one-time-password hold, the tracker opens; a voluntary change
                # goes back to the profile it started from.
                return redirect(url_for("dashboard.home" if was_held else "auth.profile"))
    status = 400 if request.method == "POST" else 200
    return render_template("change_password.html", form=form), status
