"""Games blueprint — CRUD operations, play recording, bulk updates, export."""

import datetime as dt
import io
import json
import os
from datetime import date, datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Game, PlayRecord
from app.services.labels import FORMATS
from app.utils.decorators import requires_role
from app.utils.helpers import (
    generate_unique_barcode,
    import_games_from_roster,
)

games_bp = Blueprint("games", __name__)


@games_bp.route("/games")
@login_required
def games_list():
    from app.services.machines import list_machines

    search = request.args.get("search", "").strip()
    location = request.args.get("location", "")
    status = request.args.get("status", "")
    return render_template(
        "games_list.html",
        m=list_machines(search, location, status),
        search=search,
        location_filter=location,
        status_filter=status,
    )


def _form_page(game, values, errors, status=200):
    from app.services.machine_edit import COUNTER_STATUSES, LOCATIONS, STATUSES
    from app.ui import status_label

    return render_template(
        "game_form.html", game=game, values=values, errors=errors,
        location_options=[(x, x) for x in LOCATIONS],
        status_options=[(x, status_label(x)) for x in STATUSES],
        counter_options=list(COUNTER_STATUSES.items()),
        can_set_baseline=game is None or not _has_plays(game),
    ), status


def _has_plays(game) -> bool:
    from app.services.machine_edit import has_play_history

    return has_play_history(game)


@games_bp.route("/add_game", methods=["GET", "POST"])
@login_required
@requires_role("operator")
def add_game():
    from app.services.machine_edit import (
        apply_machine_values,
        parse_machine_form,
        save_machine_image,
        set_baseline,
    )

    if request.method == "GET":
        return _form_page(None, {"location": "Warehouse", "status": "Working",
                                 "coins_per_play": 0.25, "counter_status": "Working"}, {})
    values, errors = parse_machine_form(request.form)
    image, problem = save_machine_image(request.files.get("image"),
                                        current_app.config["UPLOAD_FOLDER"])
    if problem:
        errors["image"] = problem
    if errors:
        return _form_page(None, values, errors, 400)

    taken = {b for (b,) in db.session.query(Game.barcode).all() if b}
    game = Game(barcode=generate_unique_barcode(values["name"], taken), image_filename=image)
    apply_machine_values(game, values)
    db.session.add(game)
    db.session.flush()
    message = f'Added "{game.name}".'
    if values["initial_coin_count"] is not None and game.counter_status == "Working":
        set_baseline(game, values["initial_coin_count"], "Initial baseline coin count")
        message += f" Starting meter reading: {values['initial_coin_count']:,}."
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("games.game_detail", game_id=game.id))


@games_bp.route("/game/<int:game_id>")
@login_required
def game_detail(game_id):
    """The machine page: where a scanned back-of-cabinet label lands (via /g/<barcode>).

    Everyone signed in sees the machine's status, its open work orders and its history;
    what each role can *do* here is exactly what the linked routes allow.
    """
    from app.routes.rails import verdict_of
    from app.services.machines import machine_page

    game = db.get_or_404(Game, game_id)
    page = machine_page(game)
    return render_template(
        "game_detail.html",
        page=page,
        game=game,
        rail_verdict=verdict_of(page.rail_last) if page.rail_last else None,
        priorities=["Low", "Medium", "High", "Critical"],
    )


@games_bp.route("/machine-images/<path:filename>")
@login_required
def game_image(filename):
    """A machine's photo. Uploads are saved to UPLOAD_FOLDER, outside static/, and until
    this route nothing served them, so every machine image was a broken link (F-7)."""
    from flask import send_from_directory

    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@games_bp.route("/edit_game/<int:game_id>", methods=["GET", "POST"])
@login_required
@requires_role("operator")
def edit_game(game_id):
    from app.services.machine_edit import (
        GAME_FIELDS,
        apply_machine_values,
        has_play_history,
        parse_machine_form,
        save_machine_image,
        set_baseline,
    )

    game = db.get_or_404(Game, game_id)
    if request.method == "GET":
        return _form_page(game, {k: getattr(game, k) for k in GAME_FIELDS}, {})

    values, errors = parse_machine_form(request.form)
    image, problem = save_machine_image(request.files.get("image"),
                                        current_app.config["UPLOAD_FOLDER"])
    if problem:
        errors["image"] = problem
    if errors:
        return _form_page(game, values, errors, 400)

    apply_machine_values(game, values)
    message = f'Saved "{game.name}".'
    if image:
        old = game.image_filename
        game.image_filename = image
        if old and os.path.basename(old) == old:
            try:
                os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], old))
            except OSError:
                pass
    if values["initial_coin_count"] is not None and not has_play_history(game):
        action = set_baseline(game, values["initial_coin_count"],
                              "Baseline coin count (set via edit)")
        message += f" {action} the starting meter reading: {values['initial_coin_count']:,}."
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("games.game_detail", game_id=game.id))


@games_bp.route("/record_plays/<int:game_id>", methods=["GET", "POST"])
@login_required
@requires_role("operator")
def record_plays(game_id):
    from app.services.machine_edit import last_coin_count, parse_reading, record_reading

    game = db.get_or_404(Game, game_id)
    if game.counter_status != "Working":
        state = game.counter_status.replace("_", " ").lower()
        flash(f"Plays can't be recorded for \"{game.name}\": its counter is {state}.",
              "error")
        return redirect(url_for("games.game_detail", game_id=game.id))

    last = last_coin_count(game)
    if request.method == "GET":
        return render_template("record_plays.html", game=game, last_coin_count=last,
                               values={}, errors={})
    reading, errors = parse_reading(request.form, last)
    if errors:
        return render_template("record_plays.html", game=game, last_coin_count=last,
                               values=request.form, errors=errors), 400
    record = record_reading(game, reading, last)
    db.session.commit()
    flash(f'Recorded {record.plays_count:,} plays (${record.revenue:.2f} in tokens) for "{game.name}". '
          f"Meter: {record.coin_count:,}.", "success")
    _raise_maintenance(game)
    return redirect(url_for("games.game_detail", game_id=game.id))


def _raise_maintenance(game):
    """New plays may bring preventive maintenance due (feature step 5)."""
    from app.services.upkeep import raise_due

    for order in raise_due([game]):
        flash(f"{game.name} is due for maintenance: work order #{order.id} raised.", "info")


@games_bp.route("/delete_play_record/<int:record_id>", methods=["POST"])
@login_required
@requires_role("manager")
def delete_play_record(record_id):
    record = PlayRecord.query.get_or_404(record_id)
    game_id = record.game_id
    game = Game.query.get_or_404(game_id)

    # Store values before deletion
    plays_to_subtract = record.plays_count
    revenue_to_subtract = record.revenue

    # Update game totals
    game.total_plays -= plays_to_subtract
    game.total_revenue -= revenue_to_subtract

    # Ensure totals don't go negative
    if game.total_plays < 0:
        game.total_plays = 0
    if game.total_revenue < 0:
        game.total_revenue = 0.0

    # Delete the record
    db.session.delete(record)
    db.session.commit()

    flash("Play record deleted successfully", "success")
    return redirect(url_for("games.game_detail", game_id=game_id))


@games_bp.route("/add_baseline/<int:game_id>", methods=["POST"])
@login_required
@requires_role("operator")
def add_baseline(game_id):
    game = Game.query.get_or_404(game_id)

    # Check if there are already play records
    existing_records = PlayRecord.query.filter_by(game_id=game_id).count()
    if existing_records > 0:
        flash("Cannot add baseline - play records already exist", "error")
        return redirect(url_for("games.game_detail", game_id=game_id))

    # Get coin count from form
    try:
        coin_count = int(request.form.get("baseline_coin_count", 0))
        if coin_count < 0:
            flash("Baseline coin count cannot be negative", "error")
            return redirect(url_for("games.game_detail", game_id=game_id))
    except ValueError:
        flash("Invalid coin count", "error")
        return redirect(url_for("games.game_detail", game_id=game_id))

    # Create baseline record
    baseline_record = PlayRecord(
        game_id=game_id,
        coin_count=coin_count,
        plays_count=0,
        revenue=0.0,
        date_recorded=date.today(),
        notes="Baseline coin count",
    )

    db.session.add(baseline_record)
    db.session.commit()

    flash(f"Baseline set to {coin_count} coins", "success")
    return redirect(url_for("games.game_detail", game_id=game_id))


@games_bp.route("/delete_game/<int:game_id>", methods=["POST"])
@login_required
@requires_role("admin")
def delete_game(game_id):
    """Delete a machine with its play records and work orders (F-10: nothing left dangling)."""
    from app.security.utils import log_security_event
    from app.services.machine_edit import delete_machine

    game = db.get_or_404(Game, game_id)
    name = game.name
    counts = delete_machine(game, current_app.config["UPLOAD_FOLDER"])
    db.session.commit()
    log_security_event("MACHINE_DELETED", user_id=current_user.id,
                       details=f"{name} (id={game_id}): {counts}", level="warning")
    flash(f'Deleted "{name}" with {counts["orders"]} work order(s) and {counts["plays"]} '
          "play record(s).", "success")
    return redirect(url_for("games.games_list"))


@games_bp.route("/bulk_update_games", methods=["POST"])
@login_required
@requires_role("manager")
def bulk_update_games():
    """Bulk update multiple games at once."""
    action = request.form.get("action")
    game_ids = request.form.getlist("game_ids")

    if not game_ids:
        flash("No games selected", "error")
        return redirect(url_for("games.games_list"))

    # "Export selected" lives in the same bulk form as the updates, so the selection needs no
    # JavaScript. The export itself stays the GET it always was; the POST only forwards the
    # ids, so the CSRF token never ends up in a URL.
    if action == "export":
        return redirect(url_for("games.export_selected_games", game_ids=game_ids))

    games = Game.query.filter(Game.id.in_(game_ids)).all()
    count = len(games)

    try:
        if action == "move_to_floor":
            for game in games:
                game.location = "Floor"
            flash(f"Moved {count} game(s) to Floor", "success")

        elif action == "move_to_warehouse":
            for game in games:
                game.location = "Warehouse"
            flash(f"Moved {count} game(s) to Warehouse", "success")

        elif action == "set_working":
            for game in games:
                game.status = "Working"
            flash(f"Set {count} game(s) to Working status", "success")

        elif action == "set_not_working":
            for game in games:
                game.status = "Not_Working"
            flash(f"Set {count} game(s) to Not Working status", "success")

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        flash(f"Error updating games: {str(e)}", "error")

    return redirect(url_for("games.games_list"))


@games_bp.route("/export_selected_games")
@login_required
@requires_role("manager")
def export_selected_games():
    """Export selected games to CSV."""
    game_ids = request.args.getlist("game_ids")

    if not game_ids:
        flash("No games selected", "error")
        return redirect(url_for("games.games_list"))

    games = Game.query.filter(Game.id.in_(game_ids)).all()
    data = []

    for game in games:
        date_added = game.date_added
        if date_added.tzinfo is None:
            date_added = date_added.replace(tzinfo=dt.UTC)
        days_active = (datetime.now(dt.UTC) - date_added).days or 1
        daily_revenue = game.total_revenue / days_active

        data.append(
            {
                "Game Name": game.name,
                "Manufacturer": game.manufacturer,
                "Location": game.location,
                "Status": game.status,
                "Total Plays": game.total_plays,
                "Total Revenue": game.total_revenue,
                "Daily Revenue": round(daily_revenue, 2),
                "Days Active": days_active,
            }
        )

    import csv

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "Game Name", "Manufacturer", "Location", "Status",
        "Total Plays", "Total Revenue", "Daily Revenue", "Days Active",
    ])
    writer.writeheader()
    from app.utils.helpers import csv_safe

    writer.writerows({k: csv_safe(v) for k, v in row.items()} for row in data)
    output.seek(0)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = (
        f"attachment; filename=selected_games_{len(games)}.csv"
    )
    response.headers["Content-type"] = "text/csv"

    return response


@games_bp.route("/import_games", methods=["GET", "POST"])
@login_required
@requires_role("manager")
def import_games():
    """Bulk-import games from an uploaded barcade-roster JSON file.

    Accepts the ``catbox-barcade-roster.json`` structure (``video_games`` /
    ``pinball`` / ``retired`` arrays + ``meta.platforms``). Idempotent: existing
    games (matched by name) are skipped, so re-uploading never duplicates.
    """
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("No file selected.", "error")
            return redirect(url_for("games.import_games"))

        try:
            data = json.load(file.stream)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            flash(f"Not valid JSON: {exc}", "error")
            return redirect(url_for("games.import_games"))

        if not isinstance(data, dict) or not any(
            k in data for k in ("video_games", "pinball", "retired")
        ):
            flash(
                "JSON is missing the expected 'video_games' / 'pinball' / "
                "'retired' arrays.",
                "error",
            )
            return redirect(url_for("games.import_games"))

        try:
            result = import_games_from_roster(data)
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            flash(f"Import failed: {exc}", "error")
            return redirect(url_for("games.import_games"))

        msg = (
            f"Import complete: {result['added']} added, "
            f"{result['skipped']} skipped (already present)."
        )
        flash(msg, "success" if not result["errors"] else "warning")
        for err in result["errors"][:10]:
            flash(err, "error")
        return redirect(url_for("games.games_list"))

    return render_template("import_games.html")


@games_bp.route("/scan")
@login_required
def scan():
    """Kiosk page: a focused input a USB HID barcode scanner types into.

    The form submits here (GET ?code=); a bare code or a whole label URL both land on that
    machine's /g/ address. No script needed, so it works on any browser at the bench.
    """
    raw = (request.args.get("code") or "").strip()
    if raw:
        parts = raw.rstrip("/").rsplit("/", 2)
        code = parts[-1]
        if code:
            # A place's or a part's label (/p/, /i/), and a bare code that is one of theirs
            # and not a machine's; everything else goes to /g/ exactly as before.
            kind = parts[-2] if len(parts) > 1 else ""
            if kind == "p" or (not kind and _stock_code(code) == "p"):
                return redirect(url_for("stock_labels.resolve_place", code=code))
            if kind == "i" or (not kind and _stock_code(code) == "i"):
                return redirect(url_for("stock_labels.resolve_part", code=code))
            if kind == "u" or (not kind and _stock_code(code) == "u"):
                return redirect(url_for("serials.resolve", code=code))
            return redirect(url_for("games.resolve_barcode", code=code))
    return render_template("scan.html")


def _stock_code(code: str) -> str | None:
    """"p", "i" or "u" when a bare code is a place's, a part's or a unit's, and no
    machine's."""
    from app.models import InventoryItem, SerialUnit, StockPlace

    if Game.query.filter_by(barcode=code).first() or code.isdigit():
        return None
    if StockPlace.query.filter_by(code=code.lower()).first():
        return "p"
    if InventoryItem.query.filter_by(code=code.lower()).first():
        return "i"
    if SerialUnit.query.filter_by(code=code.lower()).first():
        return "u"
    return None


@games_bp.route("/g/<code>")
@login_required
def resolve_barcode(code):
    """Resolve a scanned barcode/QR payload to the machine's page.

    **Printed labels encode this URL: it never changes.** Matches on the ``barcode`` slug
    first, then falls back to a numeric id so labels that encode the raw database id still
    resolve. It used to land on the work-order form, which readonly users may not open (F-16);
    the machine page shows everyone the machine and gives operators the report form.
    """
    game = Game.query.filter_by(barcode=code).first()
    if game is None and code.isdigit():
        game = Game.query.get(int(code))
    if game is None:
        flash(f"No machine found for scanned code '{code}'.", "error")
        return redirect(url_for("games.scan"))
    # During a collection round the label opens the meter entry instead (feature step 3).
    from app.routes.collection import round_open
    from app.services.collection import on_round

    if round_open() and current_user.has_role("operator") and on_round(game):
        return redirect(url_for("collection.stop", game_id=game.id))
    return redirect(url_for("games.game_detail", game_id=game.id))


def _picked_games():
    """The machines a label-sheet request asks for, or ``None`` after flashing why not.

    Shared by both sheets. The picker submits one "ids" field per checkbox
    (ids=1&ids=2) while a hand-written link or one built from a reprint list is more
    naturally ids=1,2,3. Both are accepted because the first version of this read only the
    first value and silently printed one label when asked for twenty-nine -- which is a fixed
    bug and not something to re-type beside a second sheet. Read from the query string or,
    for the coin-door print button, the posted form.
    """
    parts = [part for value in request.values.getlist("ids")
             for part in value.split(",") if part.strip()]
    query = Game.query
    if "ids" in request.values:
        try:
            ids = [int(part) for part in parts]
        except ValueError:
            flash("That label sheet link is malformed; pick the machines again.", "error")
            return None
        if not ids:
            flash("No machines selected.", "error")
            return None
        query = query.filter(Game.id.in_(ids))
    return query.order_by(Game.name).all()


def _base_url() -> str:
    return (current_app.config.get("BASE_URL") or request.host_url).rstrip("/")


def _label_url(game) -> str:
    """What a label's QR encodes. One definition, so the sheet and the single label
    can never disagree -- a label that encodes a different URL from its neighbour is
    not something you find out until you scan it."""
    return f"{_base_url()}/g/{game.barcode}"


def _report_label_url(game) -> str:
    """What a coin-door label's QR encodes: that machine's report form.

    Deliberately not `_label_url` with a query string added. `_label_url` builds the *public*
    label -- it goes where a tech or the bench scanner can read it, and the slug inside it is
    meant to be guessable. A report token in that URL would print a credential on the outside
    of a cabinet, where any customer could photograph it.
    """
    return f"{_base_url()}/report/{game.report_token}"


def _qr(url: str) -> str:
    import segno

    # One scale everywhere: the printed size comes from static/css/labels.css, and the
    # sheet and the single label must produce byte-identical images for the same URL.
    return segno.make(url, error="m").svg_data_uri(scale=4, border=2)


def _sheet_context(labels: list, kind: str, games: list | None = None) -> dict:
    """What every sheet page needs: the format, its pages, and any address problem.

    *games* is the selection the format links and the print button carry (by default the
    machines on the labels)."""
    from app.services.labels import address_problem, label_format, pages

    fmt = label_format(request.args.get("format"))
    example = labels[0]["url"] if labels else _base_url()
    return {
        "labels": labels, "kind": kind, "fmt": fmt, "formats": FORMATS.values(),
        "pages": pages(labels, fmt), "example_url": example,
        "address_problem": address_problem(example),
        "base_url_set": bool(current_app.config.get("BASE_URL")),
        "ids": ",".join(str(g.id) for g in (games if games is not None
                                            else [label["game"] for label in labels])),
    }


@games_bp.route("/labels")
@login_required
@requires_role("operator")
def label_sheet_pick():
    """Choose which machines go on a label sheet.

    Printing 29 labels one page at a time is the kind of chore people skip, and a
    machine without a current label is a machine GATBOX and the scan kiosk cannot
    identify -- so the picker exists to make the whole floor printable in one go.
    """
    games = Game.query.order_by(Game.name).all()
    return render_template(
        "label_sheet_pick.html",
        games=games,
        without_barcode=[g for g in games if not g.barcode],
        formats=FORMATS.values(),
    )


@games_bp.route("/labels/sheet")
@login_required
@requires_role("operator")
def label_sheet():
    """A printable sheet of public QR labels.

    Unlike the single-label page this never backfills a missing barcode. Minting an
    identifier is a decision, and a decision taken 29 at a time behind a print button
    is not one anybody reviewed -- so machines without one are listed and skipped.
    """
    selected = _picked_games()
    if selected is None:
        return redirect(url_for("games.label_sheet_pick"))
    skipped = [g for g in selected if not g.barcode]
    labels = [{"game": g, "url": _label_url(g), "qr": _qr(_label_url(g))}
              for g in selected if g.barcode]
    if not labels:
        flash("None of those machines has an ID yet, so there is nothing to print. "
              "Give each one its roster slug first.", "error")
        return redirect(url_for("games.label_sheet_pick"))
    return render_template("label_sheet.html", skipped=skipped,
                           **_sheet_context(labels, "public"))


@games_bp.route("/labels/coindoor")
@login_required
@requires_role("manager")
def coin_door_label_pick():
    """Choose which machines get a coin-door report label. Managers only (F-17): each label
    is a key that files work orders without a login."""
    games = Game.query.order_by(Game.name).all()
    locations = sorted({g.location for g in games if g.location})
    return render_template(
        "coin_door_label_pick.html",
        games=games,
        locations=locations,
        stale=[g for g in games if g.report_label_stale],
        formats=FORMATS.values(),
    )


@games_bp.route("/labels/coindoor/sheet", methods=["GET", "POST"])
@login_required
@requires_role("manager")
def coin_door_label_sheet():
    """A printable sheet of coin-door labels. These go *inside* the door.

    POST is the print button: it mints a token for any selected machine that has none and
    clears the reprint flag, then redirects to the sheet. GET only shows labels, so opening,
    refreshing or prefetching the sheet changes nothing (F-14).

    Minting here is the opposite of what `label_sheet` does about a missing barcode, and the
    difference is the point. A barcode is an identifier of record, shared with GATBOX and the
    roster, so inventing twenty-nine of them behind a print button is a decision nobody
    reviewed. A report token is an internal secret whose whole existence is the label:
    refusing to mint one would leave no way to ever print a first label. Minting is
    idempotent, so a reprint keeps the token the label already inside a door is carrying.
    """
    selected = _picked_games()
    if selected is None:
        return redirect(url_for("games.coin_door_label_pick"))
    if not selected:
        flash("No machines selected.", "error")
        return redirect(url_for("games.coin_door_label_pick"))

    if request.method == "POST":
        if "ids" not in request.form:
            # A POST must name its machines: minting a code for every machine on the floor
            # is not what an empty or truncated form should do.
            flash("No machines selected.", "error")
            return redirect(url_for("games.coin_door_label_pick"))
        fresh = [g for g in selected if not g.report_token]
        for game in selected:
            game.mint_report_token()
            # The label about to come out of the printer is the current one.
            game.report_label_stale = False
        db.session.commit()
        if fresh:
            # Minting secrets is still a decision, so the page admits to it.
            flash(f"Made {len(fresh)} new coin-door code{'' if len(fresh) == 1 else 's'} "
                  f"for {', '.join(g.name for g in fresh[:5])}"
                  f"{' and more' if len(fresh) > 5 else ''}.", "info")
        return redirect(url_for("games.coin_door_label_sheet",
                                ids=",".join(str(g.id) for g in selected),
                                format=request.form.get("format") or None))

    unprinted = [g for g in selected if not g.report_token]
    labels = [{"game": g, "url": _report_label_url(g), "qr": _qr(_report_label_url(g))}
              for g in selected if g.report_token]
    return render_template("coin_door_label_sheet.html", unprinted=unprinted,
                           **_sheet_context(labels, "coindoor", selected))


@games_bp.route("/game/<int:game_id>/report-token/rotate", methods=["POST"])
@login_required
@requires_role("manager")
def rotate_report_token(game_id):
    """Retire a machine's coin-door token. Managers only (F-17).

    The remedy when a label has been photographed, or has gone out of the door on the back of
    somebody's phone. POST only: a link that invalidates a physical label must not be
    followable by a crawler, a prefetch or a mistyped URL.

    It leaves the machine flagged for reprint, because the old label is still sitting in the
    door and still scans -- it just leads nowhere now, and nothing else on the floor would
    tell anyone that.
    """
    game = db.get_or_404(Game, game_id)
    game.rotate_report_token()
    db.session.commit()
    flash(f"{game.name}: the label in that coin door no longer works. Print a new one and "
          "swap it, or the machine cannot be reported.", "warning")
    if request.form.get("next") == "machine":
        return redirect(url_for("games.game_detail", game_id=game.id))
    return redirect(url_for("games.coin_door_label_pick"))


@games_bp.route("/game/<int:game_id>/label")
@login_required
@requires_role("operator")
def game_label(game_id):
    """Printable QR label for a game, encoding a link to its machine page."""
    game = db.get_or_404(Game, game_id)

    # Backfill a barcode for any row that somehow lacks one. Adding a machine
    # always assigns one and the roster mapping gave every existing machine its
    # roster slug, so this should never fire -- but printing is the one moment a
    # wrong identifier becomes physical and permanent, so say so out loud rather
    # than minting one silently behind a label.
    if not game.barcode:
        taken = {b for (b,) in db.session.query(Game.barcode).all() if b}
        game.barcode = generate_unique_barcode(game.name, taken)
        db.session.commit()
        flash(
            f"This machine had no ID, so one was generated from its name: "
            f"{game.barcode!r}. That is what the printed label will encode and it "
            f"is permanent. If this machine is on the roster, set its ID to the "
            f"roster slug before printing.",
            "warning",
        )

    url = _label_url(game)
    return render_template("label_sheet.html", skipped=[], single=game,
                           **_sheet_context([{"game": game, "url": url, "qr": _qr(url)}],
                                            "public"))
