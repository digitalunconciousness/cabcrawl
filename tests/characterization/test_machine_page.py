"""The machine page (/game/<id>): where a scanned back-of-cabinet label lands.

Step 4.2 of the redesign. What each role sees and can do is pinned by test_visibility; this
file checks what the page does: the quick report, the plays panel, the photo, the service.
"""
from __future__ import annotations

import io

from char_support import login
from test_routes_post import fresh, png_bytes


def page(client, floor, role, game="raider"):
    client.post("/logout")
    login(client, role)
    resp = client.get(f"/game/{floor[game]}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_quick_report_files_an_open_order_and_returns_here(client, floor):
    from app.models import MaintenanceRecord

    login(client, "operator")
    resp = client.post(f"/maintenance/game/{floor['raider']}", data={
        "status": "Open", "issue_description": "Fire button sticks", "priority": "High",
        "technician": "", "machine_down": "no"})
    assert resp.headers["Location"] == f"/game/{floor['raider']}"
    order = MaintenanceRecord.query.filter_by(issue_description="Fire button sticks").one()
    assert (order.status, order.priority, order.game_id) == ("Open", "High", floor["raider"])
    assert "Fire button sticks" in client.get(f"/game/{floor['raider']}").get_data(as_text=True)


def test_everyone_sees_open_orders_and_history(client, floor):
    body = page(client, floor, "readonly")
    assert "Joystick drifts left" in body          # open
    assert "Coin mech jammed" in body              # closed, in the history


def test_revenue_stays_with_managers(client, floor):
    """Called token value since the wording pass (2026-10-26); still managers only."""
    assert "Token value" not in page(client, floor, "operator")
    assert "Token value" in page(client, floor, "manager")


def test_a_broken_counter_says_why_plays_cannot_be_recorded(client, floor):
    body = page(client, floor, "operator", game="courier")
    assert "plays can&#39;t be recorded" in body or "plays can't be recorded" in body
    assert f"/record_plays/{floor['courier']}" not in body


def test_the_broken_counter_chip_reads_once_and_wraps_instead_of_widening_the_page(client, floor):
    """The chip said "Counter counter broken: ..." (the status label already starts with "Counter") and,
    unable to wrap, was 391 px wide: a phone scrolled sideways. Its sentence now reads once and may wrap."""
    body = page(client, floor, "operator", game="courier").replace("&#39;", "'")
    assert "Counter counter" not in body
    assert '<span class="chip chip--wrap is-warn">Counter broken: plays can\'t be recorded</span>' in body


def test_a_machine_with_no_counter_reads_the_same_way(client, floor):
    from app.extensions import db
    from app.models import Game

    db.session.get(Game, floor["courier"]).counter_status = "No_Counter"
    db.session.commit()
    body = page(client, floor, "operator", game="courier").replace("&#39;", "'")
    assert "No counter: plays can't be recorded" in body and "Counter no counter" not in body


def test_the_wrapping_chip_has_a_rule_that_lets_it_wrap():
    """The class alone does nothing; the rule is what lets a sentence break at a phone's width."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "static" / "css" / "components.css").read_text(encoding="utf-8")
    rule = re.search(r"\.chip--wrap\s*\{([^}]*)\}", css)
    assert rule, ".chip--wrap is not defined"
    assert re.search(r"white-space:\s*normal", rule.group(1)) and re.search(r"max-width:\s*100%", rule.group(1))


def test_an_operator_can_set_the_baseline_on_a_new_machine(client, floor):
    body = page(client, floor, "operator", game="courier")
    assert f'action="/add_baseline/{floor["courier"]}"' in body
    assert "add_baseline" not in page(client, floor, "readonly", game="courier")


def test_the_rail_panel_and_manual_link(client, floor):
    body = page(client, floor, "readonly")
    assert "/rails/machine/neon-raider" in body and "Held the window" in body
    assert "https://www.arcade-museum.com/Videogame/neon-raider#manuals" in body


def test_an_uploaded_game_image_can_be_displayed(client, floor, sandbox):
    """F-7, fixed: images are saved to UPLOAD_FOLDER and served by games.game_image."""
    import re

    from app.models import Game

    login(client, "operator")
    client.post("/add_game", data={"name": "Image Test",
                                   "image": (io.BytesIO(png_bytes()), "cab.png")},
                content_type="multipart/form-data")
    game = Game.query.filter_by(name="Image Test").one()
    html = client.get(f"/game/{game.id}").get_data(as_text=True)
    src = re.search(r'<img src="([^"]+%s)"' % re.escape(game.image_filename), html).group(1)
    assert client.get(src).status_code == 200


def test_the_image_route_cannot_leave_the_upload_folder(client, floor, sandbox):
    (sandbox / "secret.txt").write_text("synthetic")
    login(client, "readonly")
    assert client.get("/machine-images/../secret.txt").status_code == 404


def test_klov_slugs_match_the_old_template():
    from app.services.machines import klov_url

    assert klov_url("Ms. Pac-Man") == "https://www.arcade-museum.com/Videogame/ms--pac-man#manuals"
    assert klov_url("Tom & Jerry: The Movie!") == (
        "https://www.arcade-museum.com/Videogame/tom-and-jerry-the-movie#manuals")
    assert klov_url("Devil's (Hollow)") == "https://www.arcade-museum.com/Videogame/devils-hollow#manuals"


def test_the_service_splits_open_from_closed(app, floor):
    from app.models import Game
    from app.services.machines import machine_page

    p = machine_page(fresh(Game, floor["raider"]))
    assert [o.issue_description for o in p.open_orders] == ["Joystick drifts left"]
    assert [o.issue_description for o in p.closed_orders] == ["Coin mech jammed"]
    assert p.can_record_plays and not p.can_add_baseline
