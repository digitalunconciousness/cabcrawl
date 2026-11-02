"""Machine pages after Step 4.8a: add, edit, record plays, delete, scan, import."""
from __future__ import annotations

import io

import pytest
from char_support import login
from test_routes_post import fresh, png_bytes

# --- F-12: bad input is shown back, not a 500 ----------------------------------------------

@pytest.mark.parametrize(("year", "error"), [
    ("eighty-seven", "The year must be a number"),
    ("1800", "Use a year between"),
])
def test_a_bad_year_is_explained_on_the_form(client, floor, year, error):
    from app.models import Game

    login(client, "operator")
    resp = client.post(f"/edit_game/{floor['raider']}", data={"name": "Neon Raider",
                                                             "year": year})
    assert resp.status_code == 400 and error in resp.get_data(as_text=True)
    assert fresh(Game, floor["raider"]).year != year


@pytest.mark.parametrize(("data", "error"), [
    ({"coin_count": "150", "date": "yesterday"}, "Pick a date"),
    ({"coin_count": "150", "date": "2999-01-01"}, "can&#39;t be in the future"),
    ({"coin_count": "lots"}, "must be a whole number"),
    ({"coin_count": "1"}, "can&#39;t go backwards"),
])
def test_a_bad_reading_is_explained_and_records_nothing(client, floor, data, error):
    from app.models import PlayRecord

    before = PlayRecord.query.count()
    login(client, "operator")
    resp = client.post(f"/record_plays/{floor['raider']}", data=data)
    assert resp.status_code == 400 and error in resp.get_data(as_text=True)
    assert PlayRecord.query.count() == before


def test_a_reading_without_a_date_uses_today(client, floor):
    import datetime as dt

    from app.models import PlayRecord

    login(client, "operator")
    client.post(f"/record_plays/{floor['raider']}", data={"coin_count": "150"})
    newest = PlayRecord.query.order_by(PlayRecord.id.desc()).first()
    assert (newest.coin_count, newest.plays_count, newest.date_recorded) == (
        150, 10, dt.date.today())


@pytest.mark.parametrize(("field", "value", "error"), [
    ("name", "", "Name the machine."),
    ("coins_per_play", "a quarter", "The price must be a number"),
    ("location", "Moon", "Pick one from the list."),
    ("initial_coin_count", "-5", "can&#39;t be negative"),
])
def test_add_machine_validates_every_field(client, floor, field, value, error):
    from app.models import Game

    login(client, "operator")
    before = Game.query.count()
    data = {"name": "Galaxy Runner", field: value}
    resp = client.post("/add_game", data=data)
    assert resp.status_code == 400 and error in resp.get_data(as_text=True)
    assert Game.query.count() == before


def test_add_machine_with_a_baseline_and_photo(client, floor, sandbox):
    from app.models import Game, PlayRecord

    login(client, "operator")
    resp = client.post("/add_game", data={
        "name": "Galaxy Runner", "year": "1984", "location": "Floor",
        "initial_coin_count": "5000", "image": (io.BytesIO(png_bytes()), "cab.png")},
        content_type="multipart/form-data")
    game = Game.query.filter_by(name="Galaxy Runner").one()
    assert resp.headers["Location"] == f"/game/{game.id}"
    assert game.barcode == "galaxy-runner" and game.year == 1984
    assert game.image_filename.endswith(".jpg")
    assert (sandbox / "uploads" / game.image_filename).exists()
    assert PlayRecord.query.filter_by(game_id=game.id).one().coin_count == 5000


def test_a_photo_that_is_not_an_image_is_refused(client, floor, sandbox):
    from app.models import Game

    login(client, "operator")
    resp = client.post("/add_game", data={
        "name": "Galaxy Runner", "image": (io.BytesIO(b"not an image"), "cab.png")},
        content_type="multipart/form-data")
    assert resp.status_code == 400 and "could not be read" in resp.get_data(as_text=True)
    assert Game.query.filter_by(name="Galaxy Runner").count() == 0
    assert [p for p in (sandbox / "uploads").rglob("*") if p.is_file()] == []


def test_edit_keeps_the_barcode(client, floor):
    """Printed labels encode the barcode; renaming must never change it."""
    from app.models import Game

    login(client, "operator")
    client.post(f"/edit_game/{floor['raider']}", data={"name": "Neon Raider Deluxe"})
    game = fresh(Game, floor["raider"])
    assert (game.name, game.barcode) == ("Neon Raider Deluxe", "neon-raider")


def test_the_baseline_field_is_offered_only_before_any_plays(client, floor):
    login(client, "operator")
    assert 'name="initial_coin_count"' not in client.get(
        f"/edit_game/{floor['raider']}").get_data(as_text=True)  # it has plays
    assert 'name="initial_coin_count"' in client.get("/add_game").get_data(as_text=True)


# --- F-10: deleting a machine leaves nothing dangling --------------------------------------

def test_deleting_a_machine_removes_its_history_and_keeps_the_rail_history(client, floor,
                                                                           sandbox):
    from app.extensions import db
    from app.models import (
        Game,
        InventoryRequest,
        MaintenanceRecord,
        PlayRecord,
        RailSession,
        WorkLog,
    )

    req = fresh(InventoryRequest, floor["request"])
    req.maintenance_id = floor["open_order"]
    session = RailSession.query.filter_by(uid=floor["session_uid"]).one()
    session.game_id = floor["raider"]
    db.session.commit()
    login(client, "admin")
    client.post(f"/delete_game/{floor['raider']}")

    assert fresh(Game, floor["raider"]) is None
    assert MaintenanceRecord.query.filter_by(game_id=floor["raider"]).count() == 0
    assert WorkLog.query.filter_by(maintenance_id=floor["open_order"]).count() == 0
    assert PlayRecord.query.filter_by(game_id=floor["raider"]).count() == 0
    assert fresh(InventoryRequest, floor["request"]).maintenance_id is None
    assert RailSession.query.filter_by(uid=floor["session_uid"]).one().game_id is None


# --- scan ----------------------------------------------------------------------------------

@pytest.mark.parametrize("typed", ["neon-raider", "https://tracker.example/g/neon-raider",
                                   "https://tracker.example/g/neon-raider/"])
def test_scan_sends_a_code_or_a_label_url_to_the_machine(client, floor, typed):
    login(client, "readonly")
    resp = client.get("/scan", query_string={"code": typed})
    assert resp.headers["Location"] == "/g/neon-raider"


def test_the_scan_page_needs_no_script(client, floor):
    import re

    login(client, "readonly")
    html = client.get("/scan").get_data(as_text=True)
    assert not re.search(r"\son[a-z]+=", html)
    assert 'method="get" action="/scan"' in html
