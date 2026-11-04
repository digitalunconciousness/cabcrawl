"""The game library, read-only (contract/library-v1/): what the archive holds about the games these machines are.

Pages for any signed-in role: the list, one game, and a manual's PDF. Managers can link a machine to a game and clear a game
no machine names any more. Nothing here edits the library; that happens at the archive, and ``scripts/pull_library.py`` brings
the result back. Without an archive configured (``LIBRARY_ARCHIVE_URL``, ``LIBRARY_ARCHIVE_TOKEN``) every address here is a 404.
"""

from __future__ import annotations

from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required

from app.extensions import db
from app.models import Game, LibraryRecord
from app.services import library, library_files
from app.utils.decorators import requires_role

library_bp = Blueprint("library", __name__, url_prefix="/library")


def configured(view):
    """404 unless an archive is set up. Under the login and role checks, so a signed-out visitor still goes to the login page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not library.configured():
            abort(404)
        return view(*args, **kwargs)
    return wrapped


@library_bp.route("/")
@login_required
@configured
def index():
    return render_template("library_list.html", games=library.listing(), state=library.state(), held=library.refreshed())


@library_bp.route("/<key>")
@login_required
@configured
def game_page(key):
    if not library.valid_key(key):
        abort(404)
    lib = library.game(key)
    if lib is None:
        abort(404)
    if lib["key"] != key:                                   # a retired game: its survivor is the page
        return redirect(url_for("library.game_page", key=lib["key"]), code=302)
    machines = Game.query.filter(Game.model_key.in_(_names_for(key))).order_by(Game.name).all()
    return render_template("library_game.html", lib=lib, machines=machines, state=library.state())


def _names_for(key: str) -> list[str]:
    """The keys that mean this game: itself and the retired games that were folded into it."""
    names = {key}
    for rec in LibraryRecord.query.filter_by(kind="game").all():
        if (rec.data or {}).get("replaced_by") and library.resolve(rec.rid) == key:
            names.add(rec.rid)
    return sorted(names)


@library_bp.route("/file/<sha>")
@login_required
@configured
def manual(sha):
    """A manual's PDF, for a hash that a held document names. The bytes are in a folder no URL reaches; this is the only way to them."""
    if not library_files.valid(sha) or not library_files.held(sha):
        abort(404)
    doc = next((r for r in LibraryRecord.query.filter_by(kind="document").all() if (r.data or {}).get("sha256") == sha), None)
    if doc is None:
        abort(404)
    name = "".join(c if c.isalnum() or c in " ._-" else "_" for c in str(doc.data.get("title") or "manual")).strip() or "manual"
    response = send_file(library_files.path_for(sha), mimetype="application/pdf", as_attachment=False,
                         download_name=f"{name}.pdf", conditional=True, etag=sha, max_age=0)
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The page-wide policy says object-src 'none', which can stop a browser's own PDF viewer. This answer is a PDF, not a page,
    # so it carries the one thing that matters for it.
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


@library_bp.route("/link/<int:game_id>", methods=["POST"])
@login_required
@requires_role("manager")
@configured
def link_machine(game_id):
    """Point a machine at its game (or, with the box empty, away from it)."""
    machine = db.get_or_404(Game, game_id)
    key = (request.form.get("model_key") or "").strip().lower()
    if not key:
        if machine.model_key:
            machine.model_key = None
            db.session.commit()
            flash(f"{machine.name} is no longer linked to a game.", "success")
        return redirect(url_for("games.game_detail", game_id=machine.id))
    if not library.valid_key(key):
        flash("A game key is lowercase letters and digits with single hyphens between them, like widget-wars.", "error")
        return redirect(url_for("games.game_detail", game_id=machine.id))
    machine.model_key = key
    db.session.commit()
    if library.resolve(key) is None:
        flash(f"{machine.name} is linked to {key}. The library has not sent that game yet; it is read on the next pull.", "success")
    else:
        flash(f"{machine.name} is linked to {key}.", "success")
    return redirect(url_for("games.game_detail", game_id=machine.id))


@library_bp.route("/<key>/clear", methods=["POST"])
@login_required
@requires_role("manager")
@configured
def clear_game(key):
    """Remove the copy of a game no machine names any more (its manuals go too, unless another game's document is the same file)."""
    if not library.valid_key(key):
        abort(404)
    live = library.resolve(key) or key
    if Game.query.filter(Game.model_key.in_(_names_for(live) + [key])).first() is not None:
        flash("A machine still names this game, so it stays.", "error")
        return redirect(url_for("library.index"))
    removed = library.clear(key)
    db.session.commit()
    flash(f"Cleared {key}: {removed} record{'' if removed == 1 else 's'} removed." if removed else f"There was nothing held for {key}.",
          "success" if removed else "error")
    return redirect(url_for("library.index"))
