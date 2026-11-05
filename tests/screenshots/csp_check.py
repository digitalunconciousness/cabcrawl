#!/usr/bin/env python3
"""Load every page in a real browser and fail on any Content-Security-Policy violation.

    python tests/screenshots/csp_check.py [--library]

Development only, like take.py, whose synthetic seed, page list and sign-in it reuses. The
template scan in tests/test_csp.py catches inline script, handlers and styles in markup; this
catches what only a browser sees: anything a script injects at runtime (Chart.js, ui.js), at
phone and desktop width, signed in and signed out.

It also clicks a sortable heading, so the sorted table is checked too. Exits non-zero on a
violation and prints each one.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading

from take import ROOT, WIDTHS, build_app, pages  # noqa: F401  (ROOT sets sys.path)

LISTEN = """
window.__csp = [];
document.addEventListener("securitypolicyviolation", (e) => {
  window.__csp.push(e.violatedDirective + " blocked " + (e.blockedURI || "inline") +
                    (e.sample ? " :: " + e.sample : ""));
});
"""


def main() -> int:
    from char_support import PASSWORD
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    library = "--library" in sys.argv[1:]          # also seed a library copy and load its pages
    app, ids = build_app(tempfile.mkdtemp(prefix="arcade-csp-"), library=library)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    launch = {}
    if os.environ.get("PLAYWRIGHT_CHROMIUM"):
        launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM"]

    problems, visited = [], 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        for width, height in WIDTHS.items():
            for role in ("anon", "admin"):
                ctx = browser.new_context(viewport={"width": width, "height": height},
                                          service_workers="block")
                ctx.add_init_script(LISTEN)
                page = ctx.new_page()
                console = []
                page.on("console", lambda m, c=console: c.append(m.text)
                        if "Content Security Policy" in m.text else None)
                if role != "anon":
                    page.goto(f"{base}/login")
                    page.fill("input[name=username]", f"test-{role}")
                    page.fill("input[name=password]", PASSWORD)
                    page.click("[type=submit]")
                    page.wait_for_load_state("networkidle")
                for _name, url, who in pages(ids, library):
                    if who != role:
                        continue
                    console.clear()
                    page.goto(base + url)
                    page.wait_for_load_state("networkidle")
                    if page.locator("th .sort").count():
                        page.locator("th .sort").first.click()
                    found = page.evaluate("window.__csp") + console
                    problems += [f"{width}px {role} {url}: {v}" for v in found]
                    visited += 1
                ctx.close()
        browser.close()
    server.shutdown()

    for p in problems:
        print("CSP:", p)
    print(f"{visited} page loads, {len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
