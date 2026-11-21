"""The desktop look (the owner, 2026-11-21): each user picks Synthwave, Ledger, Graphite or Follow the system on Profile;
it applies at 1024 px and wider, and a phone always gets Synthwave (the CSS, tests/test_design_tokens.py). Like the
Tech Tracker's Account → Desktop look. Every user here is the synthetic arcade's."""
from __future__ import annotations

import re
import sys

import pytest

sys.path.insert(0, "tests/characterization")
from char_support import login
from seed import seed_floor


def theme_of(page: str) -> str | None:
    m = re.search(r'<html lang="en" data-theme="(\w+)">', page)
    return m.group(1) if m else None


def test_everyone_starts_on_synthwave_and_signed_out_pages_are_synthwave(client):
    seed_floor()
    assert theme_of(client.get("/login").get_data(as_text=True)) == "synthwave"
    login(client, "operator")
    page = client.get("/profile").get_data(as_text=True)
    assert theme_of(page) == "synthwave"
    assert "Desktop look" in page and "Phones always use Synthwave" in page
    assert re.search(r'value="synthwave"[^>]*checked|checked[^>]*value="synthwave"', page)


@pytest.mark.parametrize("theme", ["ledger", "graphite", "system", "synthwave"])
def test_a_user_picks_a_look_and_every_page_carries_it(client, app, theme):
    seed_floor()
    login(client, "readonly")
    resp = client.post("/profile/theme", data={"desk_theme": theme})
    assert resp.status_code == 302 and resp.location.endswith("/profile#look")
    for path in ("/profile", "/"):
        assert theme_of(client.get(path, follow_redirects=True).get_data(as_text=True)) == theme
    from app.models import User
    with app.app_context():
        assert User.query.filter_by(username="test-readonly").one().desk_theme == theme


def test_a_look_that_isnt_offered_is_refused_and_nothing_changes(client, app):
    seed_floor()
    login(client, "operator")
    client.post("/profile/theme", data={"desk_theme": "graphite"})
    resp = client.post("/profile/theme", data={"desk_theme": "neon-pink"}, follow_redirects=True)
    assert "Pick one of the looks." in resp.get_data(as_text=True)
    from app.models import User
    with app.app_context():
        assert User.query.filter_by(username="test-operator").one().desk_theme == "graphite"


def test_one_users_look_is_theirs_alone(client, app):
    seed_floor()
    login(client, "manager")
    client.post("/profile/theme", data={"desk_theme": "ledger"})
    from app.models import User
    with app.app_context():
        assert User.query.filter_by(username="test-admin").one().desk_theme == "synthwave"


def test_signed_out_the_route_wants_a_login(client):
    seed_floor()
    resp = client.post("/profile/theme", data={"desk_theme": "ledger"})
    assert resp.status_code == 302 and "/login" in resp.location
