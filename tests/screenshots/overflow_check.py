#!/usr/bin/env python3
"""Load each machine's page at phone widths and fail if it scrolls sideways.

    python tests/screenshots/overflow_check.py [--library] [--width 390 --width 360]

Development only, like take.py, whose synthetic seed and sign-in it reuses. A page scrolls sideways when
some box is wider than the screen, and only a browser can measure that: Star Courier's "Counter broken:
plays can't be recorded" chip could not wrap, was 391 px wide, and carried the whole page to 407 px at
390. The check looks at the three seeded machines (one working, one in the warehouse, one with a broken
counter), signed in as the admin so every panel is on the page, and prints the boxes that stick out.

The widths are the phones people scan labels with: 390 and 360. 320 is left out on purpose, because the
action buttons on every machine page are still 344 px wide there (known, and not what this checks).
Exits non-zero when a page is wider than its screen.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import threading

from take import ROOT, build_app  # noqa: F401  (ROOT sets sys.path)

WIDTHS = (390, 360)
MACHINES = ("raider", "pinball", "courier")

# The page's scroll width, then the visible boxes that stick out past the screen's edge and are not
# inside something that scrolls or clips them on purpose.
MEASURE = """(w) => {
  const wide = document.documentElement.scrollWidth, out = [];
  if (wide > w) {
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.right <= w + 0.5) continue;
      let p = el.parentElement, clipped = false;
      while (p && p !== document.body) {
        if (getComputedStyle(p).overflowX !== 'visible') { clipped = true; break; }
        p = p.parentElement;
      }
      if (!clipped) out.push(el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\\s+/).join('.') : '') + ' right=' + Math.round(r.right));
    }
  }
  return {wide, boxes: out.slice(0, 6)};
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--library", action="store_true", help="seed a library copy, so the game section is on the page")
    parser.add_argument("--width", type=int, action="append", help="a phone width to check (repeatable)")
    args = parser.parse_args()

    from char_support import PASSWORD
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.disable(logging.INFO)
    app, ids = build_app(tempfile.mkdtemp(prefix="arcade-overflow-"), library=args.library)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    launch = {}
    if os.environ.get("PLAYWRIGHT_CHROMIUM"):
        launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM"]

    problems, visited = [], 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        for width in args.width or WIDTHS:
            ctx = browser.new_context(viewport={"width": width, "height": 844}, service_workers="block")
            page = ctx.new_page()
            page.goto(f"{base}/login")
            page.fill("input[name=username]", "test-admin")
            page.fill("input[name=password]", PASSWORD)
            page.click("[type=submit]")
            page.wait_for_load_state("networkidle")
            for name in MACHINES:
                url = f"/game/{ids[name]}"
                page.goto(base + url)
                page.wait_for_load_state("networkidle")
                found = page.evaluate(MEASURE, width)
                visited += 1
                if found["wide"] > width:
                    problems.append(f"{width}px {url} ({name}) is {found['wide']}px wide: " + "; ".join(found["boxes"]))
            ctx.close()
        browser.close()
    server.shutdown()

    for p in problems:
        print("OVERFLOW:", p)
    print(f"{visited} page loads, {len(problems)} wider than the screen")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
