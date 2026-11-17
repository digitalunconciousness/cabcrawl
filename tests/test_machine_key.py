"""A new machine gets its game key from its name (the owner, 2026-11-17: new games should reach GATBOX's library and the
archive without her going to the archive). Every machine name is invented."""
from __future__ import annotations

import sys

import pytest


@pytest.mark.parametrize(("name", "key"), [
    ("Widget Wars", "widget-wars"),
    ("Widget Wars #2", "widget-wars"),
    ("Gizmo & Sprocket's Revenge", "gizmo-and-sprockets-revenge"),
    ("Café Racer II", "cafe-racer-ii"),
    ("  ", None),
    ("#3", None),
])
def test_key_from_name(app, name, key):
    from app.services.library import key_from_name

    with app.app_context():
        assert key_from_name(name) == key


def test_a_long_name_is_cut_to_a_key_that_still_holds(app):
    from app.services.library import KEY_MAX, key_from_name, valid_key

    with app.app_context():
        k = key_from_name("Widget " * 30)
        assert len(k) <= KEY_MAX and valid_key(k) and not k.endswith("-")


def test_adding_a_machine_gives_it_a_key_and_says_so(client, app):
    sys.path.insert(0, "tests/characterization")
    from char_support import login
    from seed import seed_floor

    from app.models import Game

    seed_floor()
    login(client, "operator")
    resp = client.post("/add_game", data={"name": "Gizmo Blaster #2"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Its game key is gizmo-blaster" in resp.get_data(as_text=True)
    with app.app_context():
        assert Game.query.filter_by(name="Gizmo Blaster #2").one().model_key == "gizmo-blaster"
