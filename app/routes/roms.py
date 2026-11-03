"""ROM images from GATBOX for people: the download of a dump's or a burn's image.

The file store is a folder under ``uploads/`` that no URL reaches, so these two routes are the
only way a person gets the bytes: signed in, operator and up, never a device's token (a device
reads back through ``/api/v1/files``). Everything is an attachment, never shown in the page.
"""

from __future__ import annotations

from flask import Blueprint, abort, send_file
from flask_login import login_required

from app.extensions import db
from app.models import RomBurn, RomDump
from app.services import rom_files
from app.utils.decorators import requires_role

roms_bp = Blueprint("roms", __name__, url_prefix="/roms")


def _image(record, name: str):
    try:
        response = send_file(
            rom_files.path_for(record.file.sha256), mimetype="application/octet-stream",
            as_attachment=True, download_name=name, conditional=True,
            etag=record.file.sha256, max_age=0,
        )
    except FileNotFoundError:
        abort(404)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@roms_bp.route("/dump/<int:dump_id>/image")
@login_required
@requires_role("operator")
def dump_image(dump_id):
    dump = db.get_or_404(RomDump, dump_id)
    return _image(dump, f"{dump.game.barcode}_{dump.label}_{dump.sha1[:8]}.bin")


@roms_bp.route("/burn/<int:burn_id>/image")
@login_required
@requires_role("operator")
def burn_image(burn_id):
    burn = db.get_or_404(RomBurn, burn_id)
    return _image(burn, f"{burn.game.barcode}_burned_{burn.image_sha1[:8]}.bin")
