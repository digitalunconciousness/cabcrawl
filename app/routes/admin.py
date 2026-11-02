"""Admin blueprint — user management, storage, backups."""

import os
import subprocess
import sys

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models import User
from app.security.utils import log_security_event
from app.utils.decorators import requires_role

admin_bp = Blueprint("admin", __name__)


def _users_page(revealed=None, status=200):
    from app.services.accounts import ROLE_TEXT, ROLES, history_count

    users = User.query.order_by(User.is_active.desc(), User.username.asc()).all()
    return render_template(
        "manage_users.html", users=users, roles=ROLES, role_text=ROLE_TEXT,
        history={u.id: history_count(u) for u in users}, revealed=revealed,
    ), status


@admin_bp.route("/admin/users", methods=["GET", "POST"])
@login_required
@requires_role("admin")
def manage_users():
    """Accounts: roles, active or not, password resets, deletes."""
    from app.services.accounts import one_time_password, refusal

    if request.method == "GET":
        return _users_page()

    action = request.form.get("action")
    user = db.get_or_404(User, request.form.get("user_id", type=int))
    new_role = request.form.get("new_role")
    why_not = refusal(current_user, user, action, new_role)
    if why_not:
        flash(why_not, "error")
        return redirect(url_for("admin.manage_users"))

    if action == "toggle_active":
        user.is_active = not user.is_active
        db.session.commit()
        state = "activated" if user.is_active else "deactivated"
        log_security_event("USER_ACCOUNT_TOGGLED", user_id=current_user.id,
                           details=f"Target user: {user.username} (id={user.id}), state: {state}",
                           level="warning")
        flash(f"{user.username} is {state}.", "success")

    elif action == "reset_password":
        password = one_time_password()
        user.set_password(password)
        user.must_change_password = True
        db.session.commit()
        log_security_event("USER_PASSWORD_RESET", user_id=current_user.id,
                           details=f"Target user: {user.username} (id={user.id})",
                           level="warning")
        # Shown once, in this response only: never in a flash message, which would put it in
        # the session cookie, and never in a log (F-19).
        return _users_page(revealed=(user, password))

    elif action == "change_role":
        old_role = user.role
        user.role = new_role
        db.session.commit()
        log_security_event("USER_ROLE_CHANGED", user_id=current_user.id,
                           details=f"Target user: {user.username} (id={user.id}), "
                                   f"{old_role} → {new_role}",
                           level="warning")
        flash(f"{user.username} is now {new_role}.", "success")

    elif action == "delete_user":
        log_security_event("USER_DELETED", user_id=current_user.id,
                           details=f"Target user: {user.username} (id={user.id})",
                           level="warning")
        db.session.delete(user)
        db.session.commit()
        flash(f"Deleted {user.username}.", "success")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/admin/create_user", methods=["GET", "POST"])
@login_required
@requires_role("admin")
def create_user():
    """Add an account. Leave the password blank to have a one-time one made and shown once."""
    from app.services.accounts import ROLE_TEXT, ROLES, create_user as make_user
    from app.services.accounts import validate_new_user

    values = {"role": "operator"}
    errors: dict[str, str] = {}
    if request.method == "POST":
        values = {"username": request.form.get("username", "").strip(),
                  "role": request.form.get("role", "")}
        password = request.form.get("password", "")
        errors = validate_new_user(values["username"], password, values["role"])
        if not errors:
            user, generated = make_user(values["username"], password, values["role"])
            db.session.commit()
            log_security_event("USER_CREATED", user_id=current_user.id,
                               details=f"Created user: {user.username} with role: {user.role}")
            if generated:
                return _users_page(revealed=(user, generated))
            flash(f"Added {user.username}. They choose a new password at first sign-in.",
                  "success")
            return redirect(url_for("admin.manage_users"))
    return render_template("create_user.html", values=values, errors=errors, roles=ROLES,
                           role_text=ROLE_TEXT), (400 if errors else 200)


@admin_bp.route("/admin/storage")
@login_required
@requires_role("admin")
def storage_admin():
    """Photo storage: how much, how many, and what a cleanup would remove."""
    from app.services.storage import PHOTO_MAX_AGE_DAYS, photo_stats

    return render_template("storage_admin.html", stats=photo_stats(),
                           max_age_days=PHOTO_MAX_AGE_DAYS)


@admin_bp.route("/admin/cleanup_photos", methods=["POST"])
@login_required
@requires_role("admin")
def admin_cleanup_photos():
    """Delete photos older than a year that no work order shows (F-6). Admin, like the page
    it is on (it was manager, with no page of theirs offering it)."""
    from app.services.storage import cleanup_photos

    removed = cleanup_photos()
    log_security_event("PHOTO_CLEANUP", user_id=current_user.id,
                       details=f"Removed {removed} unused photo file(s)")
    flash(f"Removed {removed} unused photo{'' if removed == 1 else 's'}.", "success")
    return redirect(url_for("admin.storage_admin"))


@admin_bp.route("/backup_management")
@login_required
@requires_role("admin")
def backup_management():
    from app.services import storage

    available = storage.in_app_backups_available()
    return render_template(
        "backup_management.html",
        available=available,
        kind=storage.database_kind(),
        backups=storage.list_backups(storage.backup_dir()) if available else [],
        deploy_dir=storage.deploy_dump_dir(),
        deploy_dumps=storage.list_backups(storage.deploy_dump_dir(),
                                          storage.DEPLOY_DUMP_NAME),
    )


def _run_script(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          cwd=os.getcwd(), timeout=300)


def _unavailable():
    flash("In-app backups only work on a SQLite database. This one is PostgreSQL: "
          "deploy/deploy.sh takes a dump before every deploy.", "error")
    return redirect(url_for("admin.backup_management"))


@admin_bp.route("/create_backup", methods=["POST"])
@login_required
@requires_role("admin")
def create_backup():
    from app.services.storage import in_app_backups_available

    if not in_app_backups_available():
        return _unavailable()
    try:
        result = _run_script(os.path.join("scripts", "backup_database.py"), "backup")
    except (OSError, subprocess.TimeoutExpired) as e:
        flash(f"The backup did not run: {e}", "error")
    else:
        if result.returncode == 0:
            log_security_event("BACKUP_CREATED", user_id=current_user.id)
            flash("Backup created.", "success")
        else:
            flash(f"Backup failed: {result.stderr.strip()[-300:]}", "error")
    return redirect(url_for("admin.backup_management"))


@admin_bp.route("/restore_backup", methods=["POST"])
@login_required
@requires_role("admin")
def restore_backup():
    from app.services.storage import backup_path, in_app_backups_available

    if not in_app_backups_available():
        return _unavailable()
    path = backup_path(request.form.get("backup_file"))
    if path is None:
        flash("That backup doesn't exist.", "error")
        return redirect(url_for("admin.backup_management"))
    try:
        result = _run_script(os.path.join("scripts", "restore_database.py"),
                             "--backup-file", path)
    except (OSError, subprocess.TimeoutExpired) as e:
        flash(f"The restore did not run: {e}", "error")
    else:
        if result.returncode == 0:
            log_security_event("BACKUP_RESTORED", user_id=current_user.id,
                               details=os.path.basename(path), level="warning")
            flash("Database restored. Restart the application.", "success")
        else:
            flash(f"Restore failed: {result.stderr.strip()[-300:]}", "error")
    return redirect(url_for("admin.backup_management"))


@admin_bp.route("/download_backup/<filename>")
@login_required
@requires_role("admin")
def download_backup(filename):
    from app.services.storage import backup_path

    path = backup_path(filename)
    if path is None:
        flash("Backup file not found.", "error")
        return redirect(url_for("admin.backup_management"))
    log_security_event("BACKUP_DOWNLOADED", user_id=current_user.id, details=filename,
                       level="warning")
    return send_file(path, as_attachment=True, download_name=filename)


@admin_bp.route("/delete_backup", methods=["POST"])
@login_required
@requires_role("admin")
def delete_backup():
    from app.services.storage import backup_path

    name = request.form.get("backup_file")
    path = backup_path(name)
    if path is None:
        flash("Backup file not found.", "error")
    else:
        os.remove(path)
        log_security_event("BACKUP_DELETED", user_id=current_user.id, details=name,
                           level="warning")
        flash(f"Deleted {name}.", "success")
    return redirect(url_for("admin.backup_management"))
