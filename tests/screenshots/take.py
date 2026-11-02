#!/usr/bin/env python3
"""Full-page screenshots of every page, at phone and desktop width, on synthetic data.

    python tests/screenshots/take.py docs/design/before
    python tests/screenshots/take.py docs/design/after --only games.games_list

Development only (Playwright is in requirements-dev.txt). It builds a throwaway SQLite
database in a temporary folder, seeds the synthetic arcade from
``tests/characterization/seed.py``, serves the app on 127.0.0.1, signs in through the real
login form (CSRF on, as in production) and captures each page the characterization matrix
knows about. It never reads ``instance/`` or ``.env``.

Files are named ``<endpoint>-<width>.png`` (``games.game_detail-390.png``), so the
before and after sets line up by name even when a URL changes.

If Playwright's bundled browser is not installed, point at any Chromium:
``PLAYWRIGHT_CHROMIUM=/path/to/chrome``.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "characterization")]

WIDTHS = {390: 844, 1280: 800}


def build_app(tmp: str):
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tmp, 'shots.db')}"
    os.environ["SECRET_KEY"] = "screenshots-only-not-a-secret"
    from app import create_app
    from app.extensions import db

    app = create_app()
    # Writes (photo uploads and the like) are not exercised; keep stray files out of the repo.
    app.config["UPLOAD_FOLDER"] = os.path.join(tmp, "uploads")
    with app.app_context():
        db.create_all()
        from seed import seed_floor

        ids = seed_floor()
        from app.models import Game

        game = db.session.get(Game, ids["raider"])
        game.mint_report_token()
        ids["report_token"] = game.report_token
        _photo(app, ids["open_order"])
        db.session.commit()
    return app, ids


def _photo(app, order_id: int) -> None:
    """A drawn (not photographed) picture on the open order, so the work-order pages show one."""
    from PIL import Image, ImageDraw

    from app.extensions import db
    from app.models import MaintenanceRecord
    from app.services.storage import photo_folder

    img = Image.new("RGB", (640, 480), (31, 15, 61))
    draw = ImageDraw.Draw(img)
    for x in range(0, 640, 40):
        draw.line([(x, 0), (x, 480)], fill=(61, 30, 107))
    draw.rectangle([180, 140, 460, 340], outline=(255, 46, 159), width=6)
    draw.ellipse([290, 210, 350, 270], fill=(125, 250, 255))
    name = f"maintenance_{order_id}_5c0ffee5.jpg"
    folder = photo_folder(app)
    os.makedirs(folder, exist_ok=True)
    img.save(os.path.join(folder, name), "JPEG", quality=85)
    db.session.get(MaintenanceRecord, order_id).add_photo(name)


def pages(ids: dict):
    """(name, url, role) for every page to capture. Role ``anon`` means signed out."""
    from test_routes_get import PAGES

    out = [("auth.login", "/login", "anon"),
           ("report.report_form", f"/report/{ids['report_token']}", "anon"),
           ("errors.404", "/no-such-page", "admin")]
    for _rule, build, _minimum, _status, _text, ctype in PAGES:
        if ctype == "text/html":
            out.append((None, build(ids), "admin"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--only", action="append", default=[],
                        help="capture only this endpoint (repeatable)")
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    from char_support import PASSWORD
    from PIL import Image

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    tmp = tempfile.mkdtemp(prefix="arcade-shots-")
    app, ids = build_app(tmp)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    adapter = app.url_map.bind("localhost")

    launch = {}
    if os.environ.get("PLAYWRIGHT_CHROMIUM"):
        launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM"]

    taken = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        for width, height in WIDTHS.items():
            for role in ("anon", "admin"):
                ctx = browser.new_context(viewport={"width": width, "height": height},
                                          device_scale_factor=1, service_workers="block")
                page = ctx.new_page()
                if role != "anon":
                    page.goto(f"{base}/login")
                    page.fill("input[name=username]", f"test-{role}")
                    page.fill("input[name=password]", PASSWORD)
                    page.click("[type=submit]")
                    page.wait_for_load_state("networkidle")
                for name, url, who in pages(ids):
                    if who != role:
                        continue
                    name = name or adapter.match(url.split("?")[0])[0]
                    if args.only and name not in args.only:
                        continue
                    page.goto(base + url)
                    page.wait_for_load_state("networkidle")
                    out = args.outdir / f"{name}-{width}.png"
                    page.screenshot(path=str(out), full_page=True)
                    # Lossless re-save: about 10% smaller. Not quantized: a baseline that is
                    # judged for colour and contrast must not have its colours rounded.
                    Image.open(out).save(out, optimize=True)
                    taken += 1
                ctx.close()
        browser.close()
    server.shutdown()
    print(f"{taken} screenshots in {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
