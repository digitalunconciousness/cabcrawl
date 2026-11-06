"""What people see of the library copy: the machine page's game section, the list, a game, a manual's PDF, and the two things a
manager can do (link a machine to a game, clear a game no machine names). Without an archive configured none of it exists.
The copy is made by the real pull job against the stub archive (tests/library_stub.py), so these pages show contract data.
Every game, machine and address is invented.
"""
from __future__ import annotations

import copy

import pytest

from tests.library_stub import MANUAL, MANUAL_SHA, example

LEAK = "someone@example.test"


def sign_in(client, role):
    from app.extensions import db
    from app.models import User

    user = User(username=f"t-{role}", role=role, must_change_password=False)
    user.set_password("not-a-real-password")
    db.session.add(user)
    db.session.commit()
    client.post("/login", data={"username": f"t-{role}", "password": "not-a-real-password"})
    return user.id


def pull():
    from app.services import library_pull

    return library_pull.run(log=lambda *_: None)


def text(resp):
    return resp.get_data(as_text=True)


@pytest.fixture()
def copy_made(deck):
    """The deck, after one pull: widget-wars is held with its manual."""
    assert pull()["ok"]
    return deck


# --- no archive, no library --------------------------------------------------------------------------------------------

def test_without_an_archive_there_is_no_library_anywhere(app, client):
    from app.extensions import db
    from app.models import Game

    game = Game(name="Widget Wars", barcode="widget-wars", status="Working", location="Floor", model_key="widget-wars")
    db.session.add(game)
    db.session.commit()
    assert client.get("/library/").status_code == 302                       # signed out: the login page, like every page
    sign_in(client, "manager")
    for path in ("/library/", "/library/widget-wars", f"/library/file/{MANUAL_SHA}"):
        assert client.get(path).status_code == 404, path
    assert client.post(f"/library/link/{game.id}", data={"model_key": "x"}).status_code == 404
    assert client.post("/library/widget-wars/clear").status_code == 404
    page = text(client.get(f"/game/{game.id}"))
    assert "Manual and limits" not in page and 'href="/library/"' not in text(client.get("/"))


# --- the machine page ---------------------------------------------------------------------------------------------------

def test_the_machine_page_shows_the_games_manual_limits_and_facts(copy_made, client):
    sign_in(client, "readonly")
    page = text(client.get(f"/game/{copy_made['machine'].id}"))
    assert "Manual and limits" in page and "Read-only copy" in page
    assert "Service manual" in page and f"/library/file/{MANUAL_SHA}" in page
    assert "+5V" in page and "4.75 V" in page and "5.25 V" in page and "Confirmed" in page
    assert "page 1" in page and "#page=1" in page
    assert "Fuse F1" in page and "3 A slow-blow" in page
    assert "/library/widget-wars" in page                                    # the full page
    assert "Save link" not in page                                           # read-only for this role


def test_a_limit_nobody_has_confirmed_says_so_and_is_not_dressed_as_a_number_to_trust(copy_made, client):
    limit = copy.deepcopy(example("changes-response-all.json")["records"][4])
    limit["rev"], limit["data"]["confirmed"] = 2, None
    copy_made["archive"].put(limit)
    pull()
    sign_in(client, "readonly")
    page = text(client.get(f"/game/{copy_made['machine'].id}"))
    assert "Unconfirmed" in page and "nobody has checked it against the page" in page and ">Confirmed<" not in page


def test_who_wrote_or_confirmed_a_record_is_never_shown(copy_made, client):
    for i in (0, 1, 4, 5):
        rec = copy.deepcopy(example("changes-response-all.json")["records"][i])
        rec["rev"], rec["by"] = 2, LEAK
        if rec["kind"] == "rail_limit":
            rec["data"]["confirmed"] = {"by": LEAK, "at": 1.0}
        copy_made["archive"].put(rec)
    pull()
    sign_in(client, "manager")
    for path in (f"/game/{copy_made['machine'].id}", "/library/", "/library/widget-wars"):
        assert LEAK not in text(client.get(path)), path


def test_a_machine_whose_game_isnt_read_yet_says_which_game_and_why_nothing_shows(deck, client):
    sign_in(client, "readonly")
    page = text(client.get(f"/game/{deck['machine'].id}"))
    assert "Not read yet" in page and "widget-wars" in page and "Manual and limits" in page
    deck["archive"].token = "gbx_a1b2c3d4e5f6.someone-elses"
    pull()
    assert "turned this application away" in text(client.get(f"/game/{deck['machine'].id}"))
    deck["stub"].stop()
    pull()
    assert "could not be reached" in text(client.get(f"/game/{deck['machine'].id}"))


def sprocket_game():
    """A game the stub archive does not hold at first: the contract's own example, renamed."""
    rec = copy.deepcopy(example("changes-response-all.json")["records"][0])
    rec["id"], rec["rev"] = "sprocket-blaster", 1
    rec["data"]["title"] = "Sprocket Blaster"
    return rec


def link_to(deck, key):
    from app.extensions import db

    deck["machine"].model_key = key
    db.session.commit()


def test_a_key_the_archive_does_not_hold_says_so_until_the_game_is_added(deck, client):
    link_to(deck, "sprocket-blaster")
    sign_in(client, "readonly")
    # Not read yet: only the pull's own words, since nothing says the archive lacks it.
    page = text(client.get(f"/game/{deck['machine'].id}"))
    assert "Not read yet" in page and "has no game with that key" not in page
    pull()
    page = text(client.get(f"/game/{deck['machine'].id}"))
    assert "The archive has no game with this key" in page and "sprocket-blaster" in page and "Not read yet" not in page
    # Someone adds the game at the archive: the next pull brings it, and the message goes.
    deck["archive"].put(sprocket_game())
    pull()
    page = text(client.get(f"/game/{deck['machine'].id}"))
    assert "Sprocket Blaster" in page and "has no game with that key" not in page


def test_a_refused_token_or_an_unreachable_archive_is_said_before_a_missing_key(deck, client):
    link_to(deck, "sprocket-blaster")
    sign_in(client, "readonly")
    pull()
    assert "has no game with that key" in text(client.get(f"/game/{deck['machine'].id}"))
    deck["archive"].token = "gbx_a1b2c3d4e5f6.someone-elses"
    pull()
    page = text(client.get(f"/game/{deck['machine'].id}"))
    assert "turned this application away" in page and "has no game with that key" not in page
    deck["stub"].stop()
    pull()
    page = text(client.get(f"/game/{deck['machine'].id}"))
    assert "could not be reached" in page and "has no game with that key" not in page


def test_a_machine_with_no_game_shows_no_section_to_operators(deck, client):
    sign_in(client, "operator")
    assert "Manual and limits" not in text(client.get(f"/game/{deck['other'].id}"))


def test_a_machine_with_no_game_shows_managers_a_link_form(deck, client):
    sign_in(client, "manager")
    page = text(client.get(f"/game/{deck['other'].id}"))
    assert "Manual and limits" in page and 'name="model_key"' in page and "Save link" in page


def test_the_link_form_is_for_managers_only(copy_made, client):
    sign_in(client, "operator")
    page = text(client.get(f"/game/{copy_made['machine'].id}"))
    assert "Manual and limits" in page and 'name="model_key"' not in page


def test_a_machine_linked_to_a_retired_game_shows_the_survivor(copy_made, client):
    from app.extensions import db

    old = copy.deepcopy(example("changes-response.json")["records"][0])
    old["id"], old["data"]["replaced_by"], old["data"]["title"] = "widget-wars-old", "widget-wars", "Widget Wars (old key)"
    copy_made["archive"].put(old)
    copy_made["machine"].model_key = "widget-wars-old"
    db.session.commit()
    pull()
    sign_in(client, "readonly")
    page = text(client.get(f"/game/{copy_made['machine'].id}"))
    assert "Service manual" in page and "/library/widget-wars" in page and "(old key)" not in page
    resp = client.get("/library/widget-wars-old")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/library/widget-wars")


# --- the list and a game --------------------------------------------------------------------------------------------------

def test_the_list_groups_games_by_whether_a_machine_names_them(copy_made, client):
    from app.extensions import db

    copy_made["other"].model_key = "pin-comet-run"
    db.session.commit()
    pull()
    copy_made["other"].model_key = None
    db.session.commit()
    sign_in(client, "readonly")
    page = text(client.get("/library/"))
    on_floor, unused = page.split("Held, but no machine names it")
    assert "Widget Wars" in on_floor and "Comet Run" in unused and "Comet Run" not in on_floor
    assert "1 of 1 limits confirmed" in on_floor and "Refreshed" in page
    assert "Clear" not in unused                                              # managers only


def test_the_list_says_what_is_wrong_when_nothing_has_been_read(deck, client):
    sign_in(client, "readonly")
    assert "Nothing has been read yet" in text(client.get("/library/"))
    deck["archive"].token = "gbx_a1b2c3d4e5f6.someone-elses"
    pull()
    page = text(client.get("/library/"))
    assert "turned this application away" in page and "Nothing has been read yet" not in page


def test_a_game_page_holds_everything_the_copy_has(copy_made, client):
    sign_in(client, "readonly")
    page = text(client.get("/library/widget-wars"))
    for expected in ("Widget Wars", "Acme", "A dedicated cabinet.", "Service manual", "+5V", "Fuse F1",
                     "ROM chips (MAME)", "widgetw", "ww_u1.bin", "deadbeef", "Known good", "dumped twice, identical"):
        assert expected in page, expected
    assert "Widget Wars" in page.split('id="machines-title"', 1)[1]            # the machine that names it
    assert client.get("/library/nope-nope").status_code == 404
    assert client.get("/library/Bad Key").status_code == 404
    assert client.get("/library/unassigned").status_code == 404


def test_a_game_the_copy_never_read_is_a_404_not_an_empty_page(copy_made, client):
    sign_in(client, "readonly")
    assert client.get("/library/pin-comet-run").status_code == 404


# --- a manual's PDF -------------------------------------------------------------------------------------------------------

def test_a_manual_opens_for_anyone_signed_in_and_only_for_a_held_document(copy_made, client):
    assert client.get(f"/library/file/{MANUAL_SHA}").status_code == 302
    sign_in(client, "readonly")
    resp = client.get(f"/library/file/{MANUAL_SHA}")
    assert resp.status_code == 200 and resp.data == MANUAL and resp.mimetype == "application/pdf"
    assert resp.headers["Content-Disposition"].startswith("inline") and "Service manual.pdf" in resp.headers["Content-Disposition"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff" and "object-src" not in resp.headers["Content-Security-Policy"]
    assert client.get(f"/library/file/{MANUAL_SHA}", headers={"Range": "bytes=0-9"}).status_code == 206
    assert client.get("/library/file/" + "0" * 64).status_code == 404
    assert client.get("/library/file/not-a-hash").status_code == 404
    assert client.get("/library/file/" + MANUAL_SHA.upper()).status_code == 404


def test_a_file_in_the_folder_that_no_document_names_is_not_served(copy_made, client):
    import hashlib

    from app.services import library_files

    body = b"%PDF-1.4 orphan"
    sha = hashlib.sha256(body).hexdigest()
    path = library_files.path_for(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    sign_in(client, "manager")
    assert client.get(f"/library/file/{sha}").status_code == 404


# --- what a manager can do ------------------------------------------------------------------------------------------------

def test_a_manager_links_a_machine_to_a_game_and_unlinks_it(deck, client):
    from app.extensions import db
    from app.models import Game

    sign_in(client, "manager")
    gid = deck["other"].id
    resp = client.post(f"/library/link/{gid}", data={"model_key": " Pin-Comet-Run "})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/game/{gid}")
    db.session.expire_all()
    assert db.session.get(Game, gid).model_key == "pin-comet-run"
    client.post(f"/library/link/{gid}", data={"model_key": ""})
    db.session.expire_all()
    assert db.session.get(Game, gid).model_key is None


@pytest.mark.parametrize("bad", ["Bad Key!", "two--hyphens", "-lead", "trail-", "a" * 61, "unassigned", "../x"])
def test_a_key_that_isnt_a_key_is_refused_and_nothing_changes(deck, client, bad):
    from app.extensions import db
    from app.models import Game

    sign_in(client, "manager")
    gid = deck["other"].id
    client.post(f"/library/link/{gid}", data={"model_key": bad})
    db.session.expire_all()
    assert db.session.get(Game, gid).model_key is None


def test_an_operator_cannot_link_or_clear(copy_made, client):
    from app.extensions import db
    from app.models import Game

    copy_made["machine"].model_key = None            # so a clear would go through if the role allowed it
    db.session.commit()
    sign_in(client, "operator")
    gid = copy_made["other"].id
    for resp in (client.post(f"/library/link/{gid}", data={"model_key": "pin-comet-run"}), client.post("/library/widget-wars/clear")):
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    db.session.expire_all()
    assert db.session.get(Game, gid).model_key is None
    from app.models import LibraryRecord

    assert LibraryRecord.query.filter_by(model_key="widget-wars").count() == 7


def test_clearing_a_game_a_machine_still_names_is_refused(copy_made, client):
    from app.models import LibraryRecord

    sign_in(client, "manager")
    client.post("/library/widget-wars/clear")
    assert LibraryRecord.query.filter_by(model_key="widget-wars").count() == 7


def test_clearing_an_unused_game_removes_it_and_a_machine_that_names_it_later_has_it_read_from_the_start(copy_made, client):
    from app.extensions import db
    from app.models import LibraryRecord
    from app.services import library, library_files

    copy_made["machine"].model_key = None
    db.session.commit()
    sign_in(client, "manager")
    assert "Clear" in text(client.get("/library/"))
    resp = client.post("/library/widget-wars/clear")
    assert resp.status_code == 302
    assert LibraryRecord.query.filter_by(model_key="widget-wars").count() == 0
    assert not library_files.held(MANUAL_SHA) and "widget-wars" not in (library.state().keys_synced or [])
    copy_made["machine"].model_key = "widget-wars"
    db.session.commit()
    pull()
    assert LibraryRecord.query.filter_by(model_key="widget-wars").count() == 7 and library_files.held(MANUAL_SHA)


# --- the roster -------------------------------------------------------------------------------------------------------------

def test_the_roster_carries_each_machines_game_key(app, client):
    from app.extensions import db
    from app.models import Device, Game

    dev = Device(name="test-bench")
    dev.public_id = "a1b2c3d4e5f6"
    token = dev.issue_token()
    db.session.add_all([dev, Game(name="Widget Wars", barcode="widget-wars", status="Working", location="Floor", model_key="widget-wars"),
                        Game(name="Sprocket", barcode="pin-sprocket", status="Working", location="Floor")])
    db.session.commit()
    body = client.get("/api/v1/roster", headers={"Authorization": f"Bearer {token}"}).get_json()
    assert {m["slug"]: m["model_key"] for m in body["machines"]} == {"widget-wars": "widget-wars", "pin-sprocket": None}


# --- the link script ----------------------------------------------------------------------------------------------------------

def test_the_link_script_links_from_the_plan_and_reports_the_rest(app, tmp_path, capsys):
    import importlib.util
    import json
    import os

    from app.extensions import db
    from app.models import Game

    db.session.add_all([Game(name="A", barcode="alpha", status="Working", location="F"),
                        Game(name="B", barcode="bravo", status="Working", location="F", model_key="by-hand"),
                        Game(name="C", barcode="charlie", status="Working", location="F", model_key="charlie-game"),
                        Game(name="D", barcode="delta", status="Working", location="F"),
                        Game(name="E", barcode="echo", status="Working", location="F"),
                        Game(name="F", barcode="foxtrot", status="Working", location="F")])
    db.session.commit()
    plan = {"games": {"alpha": {"key": "alpha-game"}, "bravo": {"key": "bravo-game"}, "charlie": {"key": "charlie-game"},
                      "delta": {"key": "Bad Key"}, "echo": {"key": "echo-game", "skip": True}}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    spec = importlib.util.spec_from_file_location("link_model_keys", os.path.join(os.path.dirname(__file__), "..", "scripts", "link_model_keys.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.main(app, [str(path)]) == 0
    out = capsys.readouterr().out
    assert "would link  A  ->  alpha-game" in out and "nothing changed" in out
    db.session.expire_all()
    assert Game.query.filter_by(barcode="alpha").one().model_key is None

    assert mod.main(app, [str(path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "linked 1; already linked 1; left alone 1 (different key), 1 (no usable key), 1 (skipped), 1 (not in the plan)" in out
    db.session.expire_all()
    keys = {g.barcode: g.model_key for g in Game.query.all()}
    assert keys == {"alpha": "alpha-game", "bravo": "by-hand", "charlie": "charlie-game", "delta": None, "echo": None, "foxtrot": None}

    assert mod.main(app, [str(path), "--apply"]) == 0
    assert "linked 0; already linked 2" in capsys.readouterr().out
    assert mod.main(app, [str(path), "--apply", "--force"]) == 0
    db.session.expire_all()
    assert Game.query.filter_by(barcode="bravo").one().model_key == "bravo-game"

    path.write_text("{")
    assert mod.main(app, [str(path)]) == 2
    path.write_text("[]")
    assert mod.main(app, [str(path)]) == 2


def test_removing_the_archive_settings_takes_the_library_away_at_once(copy_made, client):
    sign_in(client, "manager")
    assert client.get("/library/").status_code == 200
    client.application.config.update(LIBRARY_URL="", LIBRARY_TOKEN="")
    for path in ("/library/", "/library/widget-wars", f"/library/file/{MANUAL_SHA}"):
        assert client.get(path).status_code == 404, path
    assert "Manual and limits" not in text(client.get(f"/game/{copy_made['machine'].id}"))


def test_a_document_listed_but_not_downloaded_has_no_file_to_open(copy_made, client):
    doc = copy.deepcopy(example("changes-response.json")["records"][1])
    doc["id"], doc["data"]["doc_id"], doc["data"]["sha256"] = "widget-wars/later", "later", "ab" * 32
    copy_made["archive"].put(doc)
    pull()
    sign_in(client, "readonly")
    assert client.get("/library/file/" + "ab" * 32).status_code == 404
