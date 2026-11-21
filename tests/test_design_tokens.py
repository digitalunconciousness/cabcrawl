"""The design system's promises, checked against the files that make them.

* Contrast: every text token reads at WCAG AA (4.5:1) on every surface it can sit on, and
  control borders and the focus ring reach 3:1 (WCAG 1.4.11). The numbers come from
  static/css/tokens.css itself, so changing a colour there re-runs the arithmetic.
* Fonts are served from this application and carry their licences.
* Motion: anything that animates or glows is switched off under prefers-reduced-motion.
* The status table in app/ui.py covers every status value the forms can store.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "static", "css")


def _tokens() -> dict[str, str]:
    """Custom properties from :root in tokens.css, with var() references resolved."""
    text = open(os.path.join(CSS, "tokens.css"), encoding="utf-8").read()
    root = text.split("@media", 1)[0]
    raw = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root))

    def resolve(value: str) -> str:
        m = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        return resolve(raw[m.group(1)]) if m else value.strip()

    return {k: resolve(v) for k, v in raw.items()}


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TOKENS = _tokens()
SURFACES = ["--surface-page", "--surface-page-top", "--surface", "--surface-raised",
            "--surface-inset"]
TEXT = ["--text", "--text-dim", "--link", "--accent", "--ok", "--warn", "--fault", "--info",
        "--muted"]
NON_TEXT = ["--border-control", "--focus"]
FILLED = ["--accent", "--info", "--ok"]   # backgrounds that carry --text-on-accent


@pytest.mark.parametrize("fg", TEXT)
@pytest.mark.parametrize("bg", SURFACES)
def test_text_meets_aa_on_every_surface(fg, bg):
    ratio = contrast(TOKENS[fg], TOKENS[bg])
    assert ratio >= 4.5, f"{fg} on {bg} is {ratio:.2f}:1"


@pytest.mark.parametrize("token", NON_TEXT)
@pytest.mark.parametrize("bg", SURFACES)
def test_controls_and_focus_reach_three_to_one(token, bg):
    ratio = contrast(TOKENS[token], TOKENS[bg])
    assert ratio >= 3.0, f"{token} on {bg} is {ratio:.2f}:1"


@pytest.mark.parametrize("bg", FILLED)
def test_text_on_a_filled_button_meets_aa(bg):
    ratio = contrast(TOKENS["--text-on-accent"], TOKENS[bg])
    assert ratio >= 4.5, f"--text-on-accent on {bg} is {ratio:.2f}:1"


def test_the_gatbox_palette_is_carried_verbatim():
    gatbox = {"--c-bg": "#150a28", "--c-bg2": "#1f0f3d", "--c-panel": "#1f1240",
              "--c-line": "#3d1e6b", "--c-mag": "#ff2e9f", "--c-cyan": "#7dfaff",
              "--c-lime": "#5ef2b0", "--c-red": "#ff4d6d", "--c-amber": "#ffb74d",
              "--c-fg": "#ece3ff", "--c-mut": "#9080b0"}
    assert {k: TOKENS[k] for k in gatbox} == gatbox


def test_touch_target_is_at_least_44px():
    assert int(TOKENS["--tap"].rstrip("px")) >= 44


def test_fonts_are_local_and_licensed():
    base = open(os.path.join(CSS, "base.css"), encoding="utf-8").read()
    urls = re.findall(r"url\(\"?([^\")]+)\"?\)", base)
    assert urls, "base.css declares no font files"
    for url in urls:
        assert not re.match(r"https?:|//", url), url
        assert os.path.exists(os.path.normpath(os.path.join(CSS, url))), url
    fonts = os.path.join(ROOT, "static", "fonts")
    for licence in ("OFL-ChakraPetch.txt", "OFL-ShareTechMono.txt"):
        assert "SIL Open Font License" in open(os.path.join(fonts, licence)).read()


def test_share_tech_mono_is_the_unmodified_upstream_file():
    """It reserves the name 'Share'; see static/fonts/FONTS.md for why it is not converted."""
    import hashlib

    path = os.path.join(ROOT, "static", "fonts", "ShareTechMono-Regular.ttf")
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert digest == "9ceab1f87414829af259c0f537573ae03ef7dd3147c0b27a36a1a0beb6732677"


def test_reduced_motion_switches_off_scanlines_and_animation():
    base = open(os.path.join(CSS, "base.css"), encoding="utf-8").read()
    block = base.split("@media (prefers-reduced-motion: reduce)", 1)[1].split("\n}\n", 1)[0]
    assert "body::before" in block and "display: none" in block
    assert "animation-duration" in block and "transition-duration" in block


def test_every_status_a_form_can_store_has_a_tone():
    from app.forms.maintenance import MaintenanceWithInventoryForm  # noqa: F401
    from app.ui import TONES

    stored = {"Working", "Not_Working", "Being_Fixed", "Retired", "Broken_Counter",
              "No_Counter", "Open", "In_Progress", "Fixed", "Deferred", "Critical", "High",
              "Medium", "Low", "Pending", "Approved", "Ordered", "Shipped", "Received",
              "Rejected", "Urgent", "Normal"}
    templates = os.path.join(ROOT, "templates")
    offered = set()
    for name in os.listdir(templates):
        if name.endswith(".html"):
            offered |= set(re.findall(r'<option value="([A-Z][A-Za-z_]+)"',
                                      open(os.path.join(templates, name)).read()))
    offered -= {"Floor", "Warehouse"}            # locations, not statuses
    assert stored | offered <= set(TONES), sorted((stored | offered) - set(TONES))


def test_an_unknown_status_is_neutral_not_ok():
    from app.ui import status_label, status_tone

    assert status_tone("Something_New") == "muted"
    assert status_tone(None) == "muted"
    assert status_label("Not_Working") == "Not working"
    assert status_label(None) == "—"


# --- The desktop themes (2026-11-21): Ledger and Graphite at 1024 px and wider, Synthwave everywhere else -------

def _theme(name: str) -> dict[str, str]:
    """A desktop theme's tokens: :root's, with the theme's own block (inside the 64rem media query) on top."""
    text = open(os.path.join(CSS, "tokens.css"), encoding="utf-8").read()
    desk = text.split("@media (min-width: 64rem) {", 1)[1].split("\n}\n", 1)[0]
    for sel, body in re.findall(r"((?::root\[data-theme=\"\w+\"\],?\s*)+)\{([^}]*)\}", desk):
        if f'data-theme="{name}"' in sel:
            own = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
            return {**TOKENS, **{k: v.strip() for k, v in own.items()}}
    raise AssertionError(f"no desktop block for {name}")


@pytest.mark.parametrize("theme", ["ledger", "graphite"])
def test_each_desktop_theme_meets_the_same_contrast_rules(theme):
    t = _theme(theme)
    bad = [f"{fg} on {bg} {contrast(t[fg], t[bg]):.2f}" for fg in TEXT for bg in SURFACES if contrast(t[fg], t[bg]) < 4.5]
    bad += [f"{n} on {bg} {contrast(t[n], t[bg]):.2f}" for n in NON_TEXT for bg in SURFACES if contrast(t[n], t[bg]) < 3.0]
    bad += [f"--text-on-accent on {bg}" for bg in FILLED if contrast(t["--text-on-accent"], t[bg]) < 4.5]
    assert not bad, bad


def _own(block):
    return dict(re.findall(r"(--[\w-]+|color-scheme)\s*:\s*([^;]+);", block))


def _tokens_css():
    with open(os.path.join(CSS, "tokens.css"), encoding="utf-8") as f:
        return f.read()


def test_follow_system_is_ledger_unless_the_device_is_dark_then_exactly_graphite():
    text = _tokens_css()
    desk = text.split("@media (min-width: 64rem) {", 1)[1].split("\n}\n", 1)[0]
    assert ':root[data-theme="ledger"],\n  :root[data-theme="system"]' in desk
    dark = text.split("@media (min-width: 64rem) and (prefers-color-scheme: dark) {", 1)[1].split("\n}\n", 1)[0]
    graphite = desk.split(':root[data-theme="graphite"]', 1)[1]
    assert _own(dark) == _own(graphite.split("}", 1)[0])


def test_the_professional_themes_drop_glow_and_scanlines_and_use_plex():
    for theme in ("ledger", "graphite"):
        t = _theme(theme)
        assert t["--scanline-alpha"] == "0" and t["--glow-brand"] == "none" and t["--glow-fault"] == "none"
        assert t["--font-body"].startswith('"IBM Plex Sans"') and t["--font-mono"].startswith('"IBM Plex Mono"')
    with open(os.path.join(ROOT, "static", "fonts", "OFL-IBMPlex.txt"), encoding="utf-8") as f:
        assert "SIL Open Font License" in f.read()


def test_themes_are_desktop_only_so_a_phone_always_gets_synthwave():
    """The owner, 2026-11-21: like the Tech Tracker's "Desktop look". No theme block may sit outside the 64rem query."""
    text = _tokens_css()
    outside = re.sub(r"@media \(min-width: 64rem\)[^{]*\{.*?\n\}\n", "", text, flags=re.DOTALL)
    assert "data-theme" not in outside.split("/* --- Desktop themes", 1)[1].split("*/", 1)[1]
