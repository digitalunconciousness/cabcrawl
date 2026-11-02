"""Maintenance blueprint — work orders, photos, reports, PDF export."""

import datetime as dt
import io
import os
from datetime import date, datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.maintenance import MaintenanceWithInventoryForm
from app.models import (
    Game,
    InventoryItem,
    MaintenanceRecord,
    WorkLog,
)
from app.services import reservations
from app.services.branding import pdf_masthead
from app.utils.decorators import requires_role

maintenance_bp = Blueprint("maintenance", __name__)


def _p(text) -> str:
    """User text made safe for a ReportLab Paragraph, which parses its input as markup."""
    from xml.sax.saxutils import escape

    return escape(str(text or ""))


# ---------------------------------------------------------------------------
# Optional S3 copy of each photo (USE_CLOUD_STORAGE=true and the AWS_* settings, read and
# checked in app/config.py). The local copy, in uploads/maintenance_photos/ behind the
# login, is always the one the app serves; the S3 copy is a backup and is never linked.
# ---------------------------------------------------------------------------
S3_PREFIX = "maintenance_photos/"   # key prefix in the bucket (unchanged by the local move)


def _cloud_photos() -> bool:
    return bool(current_app.config.get("CLOUD_PHOTOS"))


def _upload_to_cloud(file_data, filename) -> str | None:
    """Copy a saved photo to S3 as a private object (no ACL is set, so the bucket's default,
    private, applies). Returns the object's key, or None when it wasn't copied."""
    if not _cloud_photos():
        return None
    cfg = current_app.config
    key = f"{S3_PREFIX}{filename}"
    try:
        import boto3

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=cfg["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=cfg["AWS_SECRET_ACCESS_KEY"],
            region_name=cfg["AWS_REGION"],
        )
        s3_client.put_object(
            Bucket=cfg["AWS_BUCKET_NAME"],
            Key=key,
            Body=file_data,
            ContentType="image/jpeg",
        )
        return key
    except Exception as e:  # noqa: BLE001 -- a failed copy must not lose the local photo
        current_app.logger.warning("Cloud photo copy failed for %s: %s", filename, e)
        return None


def _copy_to_cloud(path, filename):
    """``save_photos``'s on_saved hook: *path* is the file it just wrote in the photo folder."""
    with open(path, "rb") as fh:
        _upload_to_cloud(fh.read(), filename)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _inventory_choices():
    items = InventoryItem.query.order_by(InventoryItem.name.asc()).all()
    return items, [(-1, "No part")] + [
        (i.id, f"{i.name} ({i.stock_quantity} in stock)") for i in items]


def _create_order(form, game=None, machine_down=None, rail_session=None):
    """Create an order from a MaintenanceWithInventoryForm, with any parts used. Commits.
    For a machine, ``machine_down`` says whether the fault takes it out of play, and its
    status follows (app/services/downtime.py)."""
    from app.services.downtime import sync_machine_status
    from app.services.work_orders import PRIORITIES, use_parts

    priority = request.form.get("priority", "Medium")
    record = MaintenanceRecord(
        game_id=game.id if game else None,
        issue_description=form.issue_description.data,
        fix_description=form.fix_description.data,
        cost=form.cost.data if form.cost.data else None,
        technician=form.technician.data,
        status=form.status.data,
        priority=priority if priority in PRIORITIES else "Medium",
        machine_down=machine_down if game else None,
        reported_by_id=current_user.id,
        # A reading it was opened from (the rail session page's button): "prompted by".
        rail_session_id=rail_session.id if rail_session else None,
    )
    if not game:
        record.work_order_type = request.form.get("work_order_type", "general")
        record.location_description = request.form.get("location_description", "")
    if form.status.data == "Fixed":
        record.date_fixed = datetime.now(dt.UTC)
        record.fixed_by_id = current_user.id
    db.session.add(record)
    db.session.flush()
    sync_machine_status(record)

    rows = [(f.item_id.data, f.quantity_used.data or 0) for f in form.inventory_items
            if f.item_id.data and f.item_id.data != -1]
    reason = (f"Used in maintenance for {game.name} (Work Order #{record.id})" if game
              else f"Used in {record.work_order_type} maintenance (Work Order #{record.id})")
    parts = use_parts(record, rows, current_user.id, reason)
    db.session.commit()
    for warning in parts.warnings:
        flash(warning, "warning")
    return record, parts


def _places():
    """Every place parts can be taken from, for the work order forms (inventory step 6)."""
    from app.services import stock_places

    return [(n.place, n.depth) for n in stock_places.place_tree()]


def _reserve_rows(record, rows, warnings) -> int:
    """Reserve (item_id, quantity, place_id) rows for *record*; refusals become warnings."""
    from app.models import StockPlace

    done = 0
    for item_id, quantity, place_id in rows:
        item = db.session.get(InventoryItem, item_id)
        place = db.session.get(StockPlace, place_id) if place_id else None
        if item is None:
            continue
        try:
            reservations.reserve(record, item, quantity, current_user.id, place)
            done += 1
        except reservations.ReservationError as exc:
            warnings.append(str(exc))
    return done


def _rail_session_for(game, uid):
    """The rail reading a new order is opened from, if it is this machine's; else None."""
    from app.models import RailSession

    if not uid or not isinstance(uid, str):
        return None
    session = RailSession.query.filter_by(uid=uid).first()
    return session if session is not None and session.game_id == game.id else None


def _attach_photos(record) -> None:
    """Photos sent with a new order (the machine page asks for one first). Only signed-in
    staff reach this; the coin-door form takes none (owner's decision, 2026-10-25)."""
    from app.services.work_orders import save_photos

    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return
    saved, problems = save_photos(record, files,
                                  on_saved=_copy_to_cloud if _cloud_photos() else None)
    if saved:
        db.session.commit()
        flash(f"Added {saved} photo{'s' if saved != 1 else ''}.", "success")
    for problem in problems:
        flash(problem, "warning")


@maintenance_bp.route("/maintenance/game/<int:game_id>", methods=["GET", "POST"])
@login_required
@requires_role("operator")
def game_maintenance(game_id):
    """The full work-order form for one machine (the machine page has the quick version)."""
    from app.services.work_orders import PRIORITIES

    game = db.get_or_404(Game, game_id)
    form = MaintenanceWithInventoryForm()
    _items, choices = _inventory_choices()
    for row in form.inventory_items:
        row.item_id.choices = choices

    from app.services.downtime import parse_choice

    machine_down = parse_choice(request.form.get("machine_down"))
    down_error = None
    if request.method == "POST" and machine_down is None:
        down_error = "Say whether the machine can still be played."
    rail_session = _rail_session_for(game, request.values.get("rail_session"))
    if form.validate_on_submit() and down_error is None:
        record, parts = _create_order(form, game, machine_down, rail_session)
        flash(f'Maintenance record added for "{game.name}"', "success")
        _attach_photos(record)
        if parts.cost > 0:
            flash(f"Inventory items used: ${parts.cost:.2f}", "info")
        return redirect(url_for("games.game_detail", game_id=game_id))

    status = 400 if request.method == "POST" else 200
    return render_template("maintenance_with_inventory.html", form=form, game=game,
                           priorities=PRIORITIES, machine_down=machine_down,
                           down_error=down_error, rail_session=rail_session), status


@maintenance_bp.route("/maintenance/general", methods=["GET", "POST"])
@login_required
@requires_role("operator")
def general_maintenance():
    """A work order with no machine: the building, the bar, equipment."""
    from app.services.work_orders import PRIORITIES

    form = MaintenanceWithInventoryForm()
    _items, choices = _inventory_choices()
    for row in form.inventory_items:
        row.item_id.choices = choices

    if form.validate_on_submit():
        _create_order(form)
        flash("General work order created successfully!", "success")
        return redirect(url_for("maintenance.maintenance_orders"))

    status = 400 if request.method == "POST" else 200
    return render_template("maintenance_with_inventory.html", form=form, game=None,
                           priorities=PRIORITIES), status


@maintenance_bp.route("/maintenance_orders")
@login_required
def maintenance_orders():
    """Every work order: open, closed or all, searchable and sortable."""
    from app.services.upkeep import raise_due
    from app.services.work_orders import SORTS, list_orders

    # Due by days has no reading to trigger it, so it is checked here (feature step 5).
    raise_due()
    orders = list_orders(request.args.get("tab", "open"), request.args.get("search", "").strip(),
                         request.args.get("sort", ""))
    return render_template("maintenance_orders.html", o=orders, sorts=SORTS)


@maintenance_bp.route("/maintenance_detail/<int:record_id>")
@login_required
def maintenance_detail(record_id):
    """One work order: the issue, the work log, parts, photos, traces and requests."""
    from app.services.work_orders import SOURCES, load_order

    record = load_order(record_id)
    if record is None:
        abort(404)
    return render_template("maintenance_detail.html", record=record, sources=SOURCES)


def _float(name, errors, label):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        errors[name] = f"{label} must be a number."
        return None
    if value < 0:
        errors[name] = f"{label} cannot be negative."
    return value


@maintenance_bp.route("/update_maintenance/<int:record_id>", methods=["GET", "POST"])
@login_required
@requires_role("operator")
def update_maintenance(record_id):
    """Log work on an order: status, notes, time, cost, and parts used or requested."""
    from app.services.downtime import parse_choice, sync_machine_status
    from app.services.work_orders import (
        MAX_PART_ROWS,
        PRIORITIES,
        STATUSES,
        URGENCIES,
        request_parts,
        use_parts,
    )

    record = db.get_or_404(MaintenanceRecord, record_id)
    items, _choices = _inventory_choices()
    errors: dict[str, str] = {}

    if request.method == "POST":
        status = request.form.get("status", record.status)
        priority = request.form.get("priority", record.priority)
        if status not in STATUSES:
            errors["status"] = "Pick a status from the list."
        if priority and priority not in PRIORITIES:
            errors["priority"] = "Pick a priority from the list."
        cost = _float("cost", errors, "Total cost")
        time_spent = _float("time_spent", errors, "Time spent")
        work_cost = _float("work_cost", errors, "Cost of this work")

        used_rows, requested_rows, reserved_rows = [], [], []
        for i in range(MAX_PART_ROWS):
            item_id = request.form.get(f"inventory_item_{i}", "")
            qty = request.form.get(f"inventory_quantity_{i}", "")
            action = request.form.get(f"item_action_{i}", "")
            place_raw = request.form.get(f"place_{i}", "")
            place_id = int(place_raw) if place_raw.isdigit() else None
            if not item_id or item_id in ("-1", "0") or not qty:
                continue
            try:
                item_id, qty = int(item_id), int(qty)
            except ValueError:
                errors[f"part_{i}"] = f"Part row {i + 1}: the quantity must be a whole number."
                continue
            if qty <= 0:
                continue
            if action == "use":
                used_rows.append((item_id, qty, place_id))
            elif action == "reserve":
                reserved_rows.append((item_id, qty, place_id))
            elif action == "request":
                requested_rows.append((item_id, qty, request.form.get(f"urgency_{i}", "Normal")))
            else:
                errors[f"part_{i}"] = f"Part row {i + 1}: choose use or request."

        if errors:
            return render_template("update_maintenance.html", maintenance=record, items=items,
                                   places=_places(),
                                   errors=errors, form=request.form, statuses=STATUSES,
                                   priorities=PRIORITIES, urgencies=URGENCIES,
                                   rows=MAX_PART_ROWS), 400

        record.status = status
        if record.game_id is not None:
            # Older orders never said; they aren't made to now, but can be.
            choice = parse_choice(request.form.get("machine_down"))
            if choice is not None:
                record.machine_down = choice
        sync_machine_status(record)
        if priority:
            record.priority = priority
        record.fix_description = request.form.get("fix_description", record.fix_description)
        record.technician = request.form.get("technician", record.technician)
        if cost is not None:
            record.cost = cost
        if record.status == "Fixed":
            record.date_fixed = datetime.now(dt.UTC)
            record.fixed_by_id = current_user.id

        work_notes = request.form.get("work_notes", "").strip()
        if work_notes:
            db.session.add(WorkLog(maintenance_id=record.id, user_id=current_user.id,
                                   work_description=work_notes,
                                   parts_used=request.form.get("parts_used", ""),
                                   time_spent=time_spent, cost_incurred=work_cost))

        parts = use_parts(record, used_rows, current_user.id, f"Used in Work Order #{record.id}")
        requested = request_parts(record, requested_rows, current_user.id)
        # Inventory step 6: parts set aside for this order, and given back when it closes.
        reserved = _reserve_rows(record, reserved_rows, parts.warnings)
        released = reservations.release_closed(record)
        db.session.commit()

        for warning in parts.warnings:
            flash(warning, "warning")
        done = []
        if work_notes:
            done.append("Work log added")
        if parts.used:
            done.append(f"{parts.used} item(s) used (${parts.cost:.2f})")
        if reserved:
            done.append(f"{reserved} part(s) reserved")
        if released:
            done.append(f"{released} reservation(s) released, as the order is closed")
        if requested:
            done.append(f"{requested} item(s) requested")
        flash(". ".join(done) + "." if done else f"Maintenance record #{record.id} updated.",
              "success")
        return redirect(url_for("maintenance.maintenance_detail", record_id=record.id))

    return render_template("update_maintenance.html", maintenance=record, items=items,
                           places=_places(),
                           errors={}, form={}, statuses=STATUSES, priorities=PRIORITIES,
                           urgencies=URGENCIES, rows=MAX_PART_ROWS)


@maintenance_bp.route("/close_maintenance/<int:record_id>", methods=["POST"])
@login_required
@requires_role("operator")
def close_maintenance(record_id):
    """Close an order as Fixed or Deferred, with what was done and what it cost."""
    from app.services.downtime import sync_machine_status
    from app.services.work_orders import CLOSED_STATUSES

    record = db.get_or_404(MaintenanceRecord, record_id)
    status = request.form.get("status", "Fixed")
    errors: dict[str, str] = {}
    cost = _float("cost", errors, "Cost")
    if status not in CLOSED_STATUSES:
        errors["status"] = "An order is closed as Fixed or Deferred."
    if errors:
        for message in errors.values():
            flash(message, "error")
        return redirect(url_for("maintenance.maintenance_detail", record_id=record.id))

    record.status = status
    record.fix_description = request.form.get("fix_description", "")
    if cost is not None:
        record.cost = cost
    record.technician = request.form.get("technician", "")
    record.date_fixed = datetime.now(dt.UTC)
    if status == "Fixed":
        record.fixed_by_id = current_user.id
    sync_machine_status(record)
    reservations.release_closed(record)            # unused parts go back on the shelf
    db.session.commit()

    name = record.game.name if record.game else "General Maintenance"
    flash(f'Maintenance order for "{name}" marked as {record.status}!', "success")
    return redirect(url_for("maintenance.maintenance_detail", record_id=record.id))


@maintenance_bp.route("/delete_maintenance/<int:record_id>", methods=["POST"])
@login_required
@requires_role("manager")
def delete_maintenance(record_id):
    """Delete a work order (its work log goes with it)."""
    from app.services.downtime import sync_machine_status

    record = db.get_or_404(MaintenanceRecord, record_id)
    sync_machine_status(record, deleting=True)
    db.session.delete(record)
    db.session.commit()
    flash(f"Maintenance record #{record_id} deleted.", "warning")
    return redirect(url_for("maintenance.maintenance_orders"))


@maintenance_bp.route("/download_maintenance_record/<int:record_id>")
@login_required
def download_maintenance_record(record_id):
    """Generate and download PDF of maintenance record.

    Every piece of user text goes through _p(): ReportLab parses Paragraph text as markup, so
    a description containing "<b>" or "<br>" used to make the PDF a 500 (F-12).
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    record = MaintenanceRecord.query.get_or_404(record_id)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Title
    title_text = f"Work Order #{record.id}"
    if record.game:
        title_text += f" - {record.game.name}"
    else:
        title_text += " - General Maintenance"
    story.extend(pdf_masthead(styles))   # the arcade's logo (Admin, Branding)
    title = Paragraph(_p(title_text), styles["Title"])
    story.append(title)
    story.append(Spacer(1, 12))

    # Basic Info Table
    info_data = [
        ["Status:", record.status or "Open"],
        [
            "Reported:",
            (
                record.date_reported.strftime("%Y-%m-%d %H:%M")
                if record.date_reported
                else "Unknown"
            ),
        ],
    ]
    if record.date_fixed:
        info_data.append(["Fixed:", record.date_fixed.strftime("%Y-%m-%d %H:%M")])
    if record.technician:
        info_data.append(["Technician:", record.technician])
    if record.cost:
        info_data.append(["Total Cost:", f"${record.cost:.2f}"])

    info_table = Table(info_data, colWidths=[1.5 * inch, 4 * inch])
    info_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 20))

    # Original Issue
    story.append(Paragraph("<b>Original Issue:</b>", styles["Heading2"]))
    story.append(
        Paragraph(_p(record.issue_description or "No description"), styles["Normal"])
    )
    story.append(Spacer(1, 12))

    # Initial Assessment
    if record.fix_description:
        story.append(Paragraph("<b>Initial Assessment:</b>", styles["Heading2"]))
        story.append(Paragraph(_p(record.fix_description), styles["Normal"]))
        story.append(Spacer(1, 12))

    # Work Log History
    if record.work_logs:
        story.append(Paragraph("<b>Work Log History:</b>", styles["Heading2"]))
        story.append(Spacer(1, 6))

        for log in record.work_logs:
            log_header = f"{log.timestamp.strftime('%Y-%m-%d %H:%M')} - {log.user.username}"
            if log.time_spent:
                log_header += f" ({log.time_spent}h)"
            if log.cost_incurred:
                log_header += f" (${log.cost_incurred:.2f})"

            story.append(Paragraph(f"<b>{_p(log_header)}</b>", styles["Normal"]))
            story.append(Paragraph(_p(log.work_description), styles["Normal"]))
            if log.parts_used:
                story.append(
                    Paragraph(f"<i>Parts: {_p(log.parts_used)}</i>", styles["Normal"])
                )
            story.append(Spacer(1, 8))

        story.append(Spacer(1, 12))

    # Inventory Usage
    if record.inventory_usage:
        story.append(Paragraph("<b>Inventory Used:</b>", styles["Heading2"]))
        story.append(Spacer(1, 6))

        inv_data = [["Item", "Quantity", "Cost"]]
        for usage in record.inventory_usage:
            inv_data.append(
                [
                    usage.item.name,
                    str(usage.quantity_used),
                    f"${usage.total_cost:.2f}",
                ]
            )

        inv_table = Table(inv_data, colWidths=[3 * inch, 1 * inch, 1 * inch])
        inv_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ]
            )
        )
        story.append(inv_table)
        story.append(Spacer(1, 12))

    # Photos note
    photos = record.get_photos()
    if photos:
        story.append(
            Paragraph(
                f"<b>Photos:</b> {len(photos)} photo(s) attached (not included in PDF)",
                styles["Normal"],
            )
        )

    # Build PDF
    doc.build(story)
    buffer.seek(0)

    filename = f"work_order_{record.id}"
    if record.game:
        filename += f"_{record.game.name.replace(' ', '_')}"
    filename += ".pdf"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


@maintenance_bp.route(
    "/maintenance_photos/<int:maintenance_id>", methods=["GET", "POST"]
)
@login_required
@requires_role("operator")
def maintenance_photos(maintenance_id):
    """Add photos to a work order (and, for managers, remove them)."""
    from app.services.work_orders import save_photos

    record = db.get_or_404(MaintenanceRecord, maintenance_id)
    if request.method == "POST":
        saved, problems = save_photos(record, request.files.getlist("photos"),
                                      on_saved=_copy_to_cloud if _cloud_photos() else None)
        for problem in problems:
            flash(problem, "warning")
        if saved:
            db.session.commit()
            flash(f"Added {saved} photo{'s' if saved != 1 else ''}.", "success")
            return redirect(url_for("maintenance.maintenance_detail", record_id=record.id))
        if not problems:
            flash("Choose at least one photo to upload.", "warning")
        return redirect(url_for("maintenance.maintenance_photos", maintenance_id=record.id))

    return render_template("maintenance_photos.html", maintenance=record,
                           max_photos=current_app.config["MAX_PHOTOS_PER_RECORD"])


@maintenance_bp.route(
    "/delete_maintenance_photo/<int:maintenance_id>/<filename>", methods=["POST"]
)
@login_required
@requires_role("manager")
def delete_maintenance_photo(maintenance_id, filename):
    """Remove one photo from an order. Only a photo the order holds can be removed."""
    from app.services.work_orders import delete_photo

    record = db.get_or_404(MaintenanceRecord, maintenance_id)
    if delete_photo(record, filename):
        db.session.commit()
        flash("Photo deleted.", "success")
    else:
        flash("That photo is not on this work order.", "error")
    return redirect(url_for("maintenance.maintenance_detail", record_id=maintenance_id))


@maintenance_bp.route("/maintenance/photos/<filename>")
@login_required
def photo_file(filename):
    """A work-order photo, for anyone signed in: whoever can open a work order
    (maintenance_detail is login-only) can see its photos.

    Only a name the app gives photos is served (storage.PHOTO_NAME), so nothing else in
    either folder can be fetched through this, and ``<filename>`` can't hold a slash. The
    new folder is tried first, then the old static one, until
    scripts/move_maintenance_photos.py has emptied it. ``private`` keeps a shared cache
    (Cloudflare's, in front of the tunnel) from keeping a copy for the next visitor.
    """
    from app.services.storage import photo_path

    path = photo_path(filename)
    if path is None:
        abort(404)
    response = send_from_directory(os.path.dirname(path), filename)
    response.cache_control.private = True
    return response


@maintenance_bp.route("/static/maintenance_photos/<filename>")
def legacy_photo(filename):
    """The address a photo had until 2026-10-28, when Flask's static route served
    static/maintenance_photos/ to anyone. It now only redirects to photo_file, which asks
    for a login; the file itself is never sent from here.

    This rule wins over ``/static/<path:filename>``: Werkzeug tries a rule with more fixed
    text first (tests/test_photo_storage.py checks it). Other spellings of the same folder
    reach the static view, which refuses them too (app/__init__.py _guard_static_photos).
    """
    from app.services.storage import PHOTO_NAME

    if not PHOTO_NAME.match(filename):
        abort(404)
    return redirect(url_for("maintenance.photo_file", filename=filename), 302)


@maintenance_bp.route("/maintenance_reports")
@login_required
@requires_role("manager")
def maintenance_reports():
    """Generate maintenance reports with time frame filters"""
    from datetime import timedelta

    try:
        days = request.args.get("days", 30, type=int)
        if days is None or days <= 0:
            days = 30
    except (ValueError, TypeError):
        days = 30

    start_date = date.today() - timedelta(days=days)

    # Get all maintenance records in date range - USE OUTERJOIN
    all_records = (
        MaintenanceRecord.query.outerjoin(Game)
        .filter(MaintenanceRecord.date_reported >= start_date)
        .order_by(MaintenanceRecord.date_reported.desc())
        .all()
    )

    open_records = [r for r in all_records if r.status in ["Open", "In_Progress"]]
    closed_records = [r for r in all_records if r.status in ["Fixed", "Deferred"]]

    # Calculate statistics
    total_cost = sum(r.cost or 0 for r in closed_records)
    avg_resolution_days = 0
    if closed_records:
        resolution_times = []
        for r in closed_records:
            if r.date_fixed and r.date_reported:
                days_to_fix = (r.date_fixed.date() - r.date_reported.date()).days
                resolution_times.append(max(1, days_to_fix))
        if resolution_times:
            avg_resolution_days = sum(resolution_times) / len(resolution_times)

    return render_template(
        "maintenance_reports.html",
        all_records=all_records,
        open_records=open_records,
        closed_records=closed_records,
        days_filter=days,
        start_date=start_date,
        total_cost=total_cost,
        avg_resolution_days=avg_resolution_days,
    )


@maintenance_bp.route("/export_maintenance_report")
@login_required
@requires_role("manager")
def export_maintenance_report():
    """Export maintenance report as PDF"""
    import matplotlib
    matplotlib.use("Agg")
    from datetime import timedelta

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    report_type = request.args.get("type", "all")
    try:
        days = request.args.get("days", 30, type=int)
        if days is None or days <= 0:
            days = 30
    except (ValueError, TypeError):
        days = 30

    start_date = date.today() - timedelta(days=days)

    # Get records based on type - USE OUTERJOIN for general facility maintenance
    if report_type == "open":
        records = (
            MaintenanceRecord.query.outerjoin(Game)
            .filter(MaintenanceRecord.status.in_(["Open", "In_Progress"]))
            .order_by(MaintenanceRecord.date_reported.desc())
            .all()
        )
        title = "Open Maintenance Orders"
    elif report_type == "closed":
        records = (
            MaintenanceRecord.query.outerjoin(Game)
            .filter(
                MaintenanceRecord.status.in_(["Fixed", "Deferred"]),
                MaintenanceRecord.date_reported >= start_date,
            )
            .order_by(MaintenanceRecord.date_reported.desc())
            .all()
        )
        title = f"Closed Maintenance Orders (Last {days} Days)"
    else:
        records = (
            MaintenanceRecord.query.outerjoin(Game)
            .filter(MaintenanceRecord.date_reported >= start_date)
            .order_by(MaintenanceRecord.date_reported.desc())
            .all()
        )
        title = f"All Maintenance Orders (Last {days} Days)"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    story.extend(pdf_masthead(styles))   # the arcade's logo (Admin, Branding)
    # Title
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 12))

    # Summary stats
    total_records = len(records)
    open_count = len([r for r in records if r.status in ["Open", "In_Progress"]])
    closed_count = len([r for r in records if r.status in ["Fixed", "Deferred"]])
    total_cost = sum(
        r.cost or 0 for r in records if r.status in ["Fixed", "Deferred"]
    )

    summary_data = [
        ["Metric", "Value"],
        ["Total Records", str(total_records)],
        ["Open Orders", str(open_count)],
        ["Closed Orders", str(closed_count)],
        ["Total Cost", f"${total_cost:.2f}"],
    ]

    summary_table = Table(summary_data)
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 14),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )

    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Maintenance records table
    if records:
        story.append(Paragraph("Maintenance Records", styles["Heading2"]))

        maintenance_data = [
            ["Game", "Issue", "Status", "Date", "Cost", "Work Summary"]
        ]

        for record in records[:15]:
            work_summary = "No work logged"
            if hasattr(record, "work_logs") and record.work_logs:
                latest_work = record.work_logs[-1]
                work_summary = (
                    latest_work.work_description[:35] + "..."
                    if len(latest_work.work_description) > 35
                    else latest_work.work_description
                )
            elif record.work_notes:
                work_summary = (
                    record.work_notes[:35] + "..."
                    if len(record.work_notes) > 35
                    else record.work_notes
                )
            elif record.fix_description:
                work_summary = (
                    record.fix_description[:35] + "..."
                    if len(record.fix_description) > 35
                    else record.fix_description
                )
            elif record.status in ["Open", "In_Progress"]:
                work_summary = "In progress..."

            game_name = record.game.name if record.game else "General Facility"
            game_name = game_name[:12] + "..." if len(game_name) > 12 else game_name

            maintenance_data.append(
                [
                    game_name,
                    (
                        record.issue_description[:20] + "..."
                        if len(record.issue_description) > 20
                        else record.issue_description
                    ),
                    record.status.replace("_", " "),
                    record.date_reported.strftime("%m/%d"),
                    f"${record.cost:.0f}" if record.cost else "$0",
                    work_summary,
                ]
            )

        col_widths = [90, 120, 60, 40, 40, 190]

        maintenance_table = Table(maintenance_data, colWidths=col_widths)
        maintenance_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("TOPPADDING", (0, 1), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("WORDWRAP", (0, 0), (-1, -1), True),
                ]
            )
        )

        story.append(maintenance_table)

        # Detailed work log section
        work_log_records = [
            r
            for r in records[:10]
            if hasattr(r, "work_logs") and r.work_logs
        ]
        if work_log_records:
            story.append(Spacer(1, 20))
            story.append(
                Paragraph(
                    "Detailed Work Logs (Recent Orders)", styles["Heading2"]
                )
            )

            for record in work_log_records:
                story.append(Spacer(1, 12))
                game_name_full = (
                    record.game.name if record.game else "General Facility"
                )
                story.append(
                    Paragraph(
                        f"<b>{_p(game_name_full)}</b> - Work Order #{record.id}",
                        styles["Heading3"],
                    )
                )
                story.append(
                    Paragraph(
                        f"<i>Issue: {_p(record.issue_description[:80])}"
                        f"{'...' if len(record.issue_description) > 80 else ''}</i>",
                        styles["Normal"],
                    )
                )
                story.append(Spacer(1, 8))

                for i, work_log in enumerate(record.work_logs[-3:], 1):
                    work_text = (
                        f"<b>Entry {i}:</b> "
                        f"{work_log.timestamp.strftime('%m/%d %H:%M')} - "
                        f"{_p(work_log.user.username)}<br/>"
                    )
                    work_text += (
                        f"{_p(work_log.work_description[:120])}"
                        f"{'...' if len(work_log.work_description) > 120 else ''}"
                    )
                    if work_log.time_spent:
                        work_text += f"<br/><i>Time: {work_log.time_spent}h</i>"
                    if work_log.cost_incurred:
                        work_text += f" <i>Cost: ${work_log.cost_incurred:.2f}</i>"

                    story.append(Paragraph(work_text, styles["Normal"]))
                    story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)

    filename = f"maintenance_report_{report_type}_{days}days.pdf"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )
