"""Nothing this application serves may load anything from somewhere else.

The house rule is "no CDN assets". It had been broken twice without anyone noticing --
Bootstrap from cdn.jsdelivr.net on the GPIO test page, and Google Fonts imported at the
top of the main stylesheet -- while two dead Content-Security-Policy definitions sat in
the codebase naming two *different* CDNs, neither of them the ones actually in use, and
the live answer was no policy at all.

So this is asserted rather than remembered. These tests fail on the thing that is easy
to do by accident: pasting a `<link>` or an `@import` that points at a CDN because it is
quicker than vendoring the file.
"""
from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hosts that serve assets. A reference to one of these from a template or a stylesheet
# means a page load reaches out to a third party.
ASSET_HOSTS = re.compile(
    r"""https?://(?:
        [a-z0-9-]*\.?(?:cdnjs\.cloudflare|jsdelivr|unpkg|jquery|bootstrapcdn)\.(?:com|net)
      | fonts\.(?:googleapis|gstatic)\.com
      | ajax\.(?:googleapis|aspnetcdn)\.com
      | cdn\.[a-z0-9-]+\.[a-z]{2,}
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Only these actually fetch something when a page loads. An <a href> to an external site
# is a link, not an asset, and a URL inside a comment is documentation.
FETCHING = re.compile(
    r"""(?:
        <link[^>]+href\s*=            # stylesheets, preloads, icons
      | <script[^>]+src\s*=           # scripts
      | <img[^>]+src\s*=              # images
      | <iframe[^>]+src\s*=
      | @import\s                     # CSS imports
      | \bsrc\s*:\s*url\(             # @font-face
      | \bbackground(?:-image)?\s*:[^;]*url\(
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _files(*subdirs: str, suffixes: tuple[str, ...]) -> list[str]:
    found = []
    for sub in subdirs:
        for root, _dirs, names in os.walk(os.path.join(REPO_ROOT, sub)):
            for name in names:
                if name.endswith(suffixes):
                    found.append(os.path.join(root, name))
    return sorted(found)


def _strip_comments(text: str, path: str) -> str:
    """Drop comments, so a URL named in an explanation is not a violation."""
    if path.endswith((".css", ".js")):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    if path.endswith(".html"):
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


@pytest.mark.parametrize("path", _files("templates", "static", suffixes=(".html", ".css")))
def test_no_page_or_stylesheet_fetches_from_a_cdn(path):
    body = _strip_comments(open(path, encoding="utf-8", errors="replace").read(), path)
    for match in ASSET_HOSTS.finditer(body):
        line_start = body.rfind("\n", 0, match.start()) + 1
        context = body[max(0, match.start() - 220):match.end()]
        if FETCHING.search(context):
            rel = os.path.relpath(path, REPO_ROOT)
            line = body.count("\n", 0, line_start) + 1
            pytest.fail(
                f"{rel}:{line} loads {match.group(0)} at page load. "
                f"Vendor the file into static/ instead."
            )


def test_the_fonts_are_in_the_repository_with_their_licence():
    """Every font file is one base.css declares, and each family's licence travels with it.
    (The pre-redesign Orbitron files went with cyberpunk.css in Step 4.8d.)"""
    fonts = os.path.join(REPO_ROOT, "static", "fonts")
    css = open(os.path.join(REPO_ROOT, "static", "css", "base.css"), encoding="utf-8").read()
    declared = set(re.findall(r'url\("\.\./fonts/([^"]+)"\)', css))
    files = {n for n in os.listdir(fonts) if n.endswith((".woff2", ".woff", ".ttf", ".otf"))}
    assert files == declared, f"undeclared: {files - declared}, missing: {declared - files}"
    for name in files:
        if name.endswith(".woff2"):
            with open(os.path.join(fonts, name), "rb") as fh:
                assert fh.read(4) == b"wOF2", f"{name} is not a woff2 file"
    for licence in ("OFL-ChakraPetch.txt", "OFL-ShareTechMono.txt"):
        assert "SIL OPEN FONT LICENSE" in open(os.path.join(fonts, licence),
                                               encoding="utf-8").read().upper()


# --- the policy that enforces it at runtime --------------------------------

def test_every_response_carries_the_policy(client):
    response = client.get("/login")
    csp = response.headers.get("Content-Security-Policy")
    assert csp, "no Content-Security-Policy header"
    for directive in ("default-src 'self'", "font-src 'self'", "object-src 'none'",
                      "base-uri 'self'", "form-action 'self'", "frame-ancestors 'self'"):
        assert directive in csp, directive
    assert "img-src 'self' data:" in csp, "QR labels are inline SVG data: URIs"


def test_the_policy_allows_no_external_origin(client):
    """The failure this guards: adding a host to the policy to make a CDN work."""
    csp = client.get("/login").headers["Content-Security-Policy"]
    assert "http://" not in csp and "https://" not in csp, csp
    assert "*" not in csp, csp


def test_the_other_security_headers_are_still_sent(client):
    headers = client.get("/login").headers
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_there_is_exactly_one_policy_in_the_codebase():
    """Two dead policies naming two different CDNs is how the live answer went
    unnoticed. If a second one appears, it is dead or it is a conflict.

    Two exceptions, on purpose: an uploaded SVG logo is served with a stricter policy of its
    own (sandbox, no script), which the app-wide one leaves in place (2026-10-26); and a
    library manual's PDF (app/routes/library.py) carries only ``frame-ancestors``, because it is a
    PDF and not a page, and the app-wide ``object-src 'none'`` can stop a browser's own PDF viewer
    (2026-11-04)."""
    hits = []
    for path in _files("app", suffixes=(".py",)):
        body = open(path, encoding="utf-8").read()
        # Only count assignments, not the words in an explanatory comment.
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"""Content-Security-Policy["']\]?\s*(=|:)""", line) or \
               re.match(r"^CSP\s*=", stripped):
                hits.append(os.path.relpath(path, REPO_ROOT))
    assert hits == ["app/__init__.py", "app/routes/branding.py", "app/routes/library.py"], hits
