"""The library pull job (app/services/library_pull.py, scripts/pull_library.py) against a stub archive that speaks the
vendored read-side contract (contract/library-v1/). The stub (tests/library_stub.py) starts from the contract's own
examples, and the first test asks it the contract's own requests, so the two ends can't drift apart unseen.
Every game, machine and byte is invented (they are the contract's).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.library_stub import (
    EXAMPLES,
    MANUAL,
    MANUAL_SHA,
    TOKEN,
    canonical_hash,
    example,
)


def run():
    from app.services import library_pull

    return library_pull.run(log=lambda *_: None)


def held(kind=None):
    from app.models import LibraryRecord

    q = LibraryRecord.query
    if kind:
        q = q.filter_by(kind=kind)
    return {(r.kind, r.rid): r for r in q.all()}


def asks(archive, prefix):
    return [p for p, _ in archive.requests if p.startswith(prefix)]


def feed_asks(archive):
    out = []
    for path, _ in archive.requests:
        u = urlsplit(path)
        if u.path == "/api/v1/library/changes":
            q = parse_qs(u.query)
            out.append({"since": int(q["since"][0]), "limit": int(q["limit"][0]), "models": q["models"][0].split(",")})
    return out


def get(stub, path, headers=None):
    req = urllib.request.Request(stub.url + path, headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})})
    with urllib.request.urlopen(req) as r:
        return r.status, r.read(), dict(r.headers)


# --- the stub is the contract's examples --------------------------------------------------------------------------------

def test_the_stub_answers_the_contracts_own_requests_with_the_contracts_own_examples(stub):
    asked = {
        "changes-response.json": "/api/v1/library/changes?since=0&limit=4&models=widget-wars",
        "changes-response-filtered.json": "/api/v1/library/changes?since=8&limit=1&models=widget-wars",
        "changes-response-last.json": "/api/v1/library/changes?since=8&limit=4&models=widget-wars",
        "changes-response-empty.json": "/api/v1/library/changes?since=11&limit=4&models=widget-wars",
        "changes-response-all.json": "/api/v1/library/changes?since=0&limit=500&models=widget-wars,pin-comet-run",
    }
    for name, path in asked.items():
        status, body, _ = get(stub, path)
        assert (status, json.loads(body)) == (200, example(name)), name
    status, body, headers = get(stub, f"/api/v1/files/{MANUAL_SHA}", {"Range": "bytes=0-127"})
    assert (status, body) == (206, MANUAL[:128])
    assert headers["Content-Range"] == "bytes 0-127/590"


# --- what the job asks for and sends ----------------------------------------------------------------------------------

def test_it_asks_for_its_own_machines_games_only_and_sends_nothing_but_the_token(deck):
    out = run()
    assert out["ok"] and not out["refused"] and out["error"] is None
    asked = feed_asks(deck["archive"])
    assert asked and all(a["models"] == ["widget-wars"] for a in asked)
    assert all(a["limit"] <= 500 for a in asked)
    assert {k for k in held()} == {("game", "widget-wars"), ("document", "widget-wars/service-manual"), ("mame", "widget-wars"),
                                   ("rom_list", "widget-wars"), ("rail_limit", "c0ffee00c0ffee00c0ffee00c0ffee00"),
                                   ("fact", "f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1"), ("rom_image", "b" * 64)}
    allowed = {"host", "user-agent", "authorization", "accept-encoding", "connection", "range"}
    for _, headers in deck["archive"].requests:
        lower = {k.lower(): v for k, v in headers.items()}
        assert lower["authorization"] == f"Bearer {TOKEN}"
        assert set(lower) <= allowed, set(lower) - allowed


def test_the_access_service_token_is_sent_when_it_is_set_and_not_otherwise(deck, app):
    run()
    assert all("cf-access-client-id" not in {k.lower() for k in h} for _, h in deck["archive"].requests)
    deck["archive"].requests.clear()
    app.config.update(LIBRARY_ACCESS_ID="abc.access", LIBRARY_ACCESS_SECRET="s3cret")
    run()
    assert deck["archive"].requests
    for _, h in deck["archive"].requests:
        lower = {k.lower(): v for k, v in h.items()}
        assert (lower["cf-access-client-id"], lower["cf-access-client-secret"]) == ("abc.access", "s3cret")


def test_an_unconfigured_application_does_nothing(deck, app):
    app.config.update(LIBRARY_URL="", LIBRARY_TOKEN="")
    out = run()
    assert out["configured"] is False and out["ok"] is False
    assert deck["archive"].requests == [] and held() == {}


def test_keys_that_arent_keys_are_never_asked_for(deck):
    from app.extensions import db
    from app.models import Game

    db.session.add_all([Game(name="x", barcode="bad-1", status="Working", location="F", model_key="Bad Key!"),
                        Game(name="y", barcode="bad-2", status="Working", location="F", model_key="unassigned"),
                        Game(name="z", barcode="bad-3", status="Working", location="F", model_key="a" * 61)])
    db.session.commit()
    run()
    assert {k for a in feed_asks(deck["archive"]) for k in a["models"]} == {"widget-wars"}


def test_a_floor_with_no_linked_machine_asks_for_nothing(deck):
    from app.extensions import db

    deck["machine"].model_key = None
    db.session.commit()
    out = run()
    assert out["ok"] and deck["archive"].requests == [] and held() == {}


# --- records: replaced, removed, followed -----------------------------------------------------------------------------

def test_a_tombstone_removes_the_copy_and_a_changed_record_replaces_it(deck):
    from app.extensions import db
    from app.models import LibraryRecord

    # A copy that an earlier run made of the fact the archive has since deleted (the example's tombstone at 11):
    db.session.add(LibraryRecord(kind="fact", rid="e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1", model_key="widget-wars", rev=1, hash="x",
                                 data={"what": "old"}))
    db.session.commit()
    run()
    assert ("fact", "e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1") not in held()
    assert held("game")[("game", "widget-wars")].data["title"] == example("changes-response.json")["records"][0]["data"]["title"]

    game = copy.deepcopy(example("changes-response.json")["records"][0])
    game["rev"], game["data"]["title"] = 2, "Widget Wars II"
    deck["archive"].put(game)
    out = run()
    assert out["changed"] == 1
    row = held("game")[("game", "widget-wars")]
    assert (row.rev, row.data["title"]) == (2, "Widget Wars II")


def test_the_second_run_reads_from_where_the_first_stopped(deck):
    run()
    first = len(feed_asks(deck["archive"]))
    run()
    again = feed_asks(deck["archive"])[first:]
    assert [a["since"] for a in again] == [deck["archive"].head]


def test_a_key_a_machine_gains_is_read_from_the_start_once_and_the_old_keys_from_the_cursor(deck):
    from app.extensions import db

    run()
    deck["archive"].requests.clear()
    deck["other"].model_key = "pin-comet-run"
    db.session.commit()
    run()
    asked = feed_asks(deck["archive"])
    assert asked[0] == {"since": 0, "limit": 500, "models": ["pin-comet-run"]}
    assert all(a["models"] == ["widget-wars"] and a["since"] == deck["archive"].head for a in asked[1:])
    assert ("game", "pin-comet-run") in held() and ("fact", "abababababababababababababababab") in held()
    deck["archive"].requests.clear()
    run()
    assert [a["since"] for a in feed_asks(deck["archive"])] == [deck["archive"].head]


def test_a_retired_game_is_followed_to_its_survivor(deck):
    from app.extensions import db
    from app.services import library

    old = copy.deepcopy(example("changes-response.json")["records"][0])
    old["id"] = "widget-wars-old"
    old["data"]["replaced_by"] = "widget-wars"
    deck["archive"].put(old)
    deck["machine"].model_key = "widget-wars-old"
    db.session.commit()
    run()
    assert ("game", "widget-wars-old") in held() and ("game", "widget-wars") in held()
    assert library.game("widget-wars-old")["key"] == "widget-wars"
    assert {k for a in feed_asks(deck["archive"]) for k in a["models"]} == {"widget-wars-old", "widget-wars"}


def test_records_of_a_kind_it_doesnt_know_or_a_malformed_one_are_left_out(app):
    from app.services import library_pull

    good = {"kind": "fact", "id": "f" * 32, "rev": 1, "hash": "h", "data": {"model_key": "widget-wars", "what": "x"}}
    n = library_pull.apply_records([
        good,
        {"kind": "cartridge", "id": "z", "rev": 1, "hash": "h", "data": {}},                     # a kind from a later edition
        {"kind": "fact", "id": "g" * 32, "rev": "one", "hash": "h", "data": {}},               # revision isn't a number
        {"kind": "fact", "id": "h" * 32, "rev": 1, "hash": "h", "data": "text"},               # data isn't an object
        "garbage",
    ])
    assert n == 1 and set(held()) == {("fact", "f" * 32)}


# --- the archive is restored from an older copy ---------------------------------------------------------------------------

def test_a_restored_archive_is_read_again_from_the_start_and_the_copy_trimmed_to_it(deck):
    from app.services import library

    run()
    assert len(held()) == 7
    state = library.state()
    assert state.cursor == deck["archive"].head == 11

    arch = deck["archive"]
    arch.rows = [(s, r) for s, r in arch.rows if s <= 4]                     # the older copy: game, document, MAME set, ROM list
    arch.head = 4
    older = arch.rows[0][1]
    older["data"]["title"] = "Widget Wars (older copy)"
    older["hash"] = canonical_hash(older["data"])
    out = run()
    assert out["restored"] and out["ok"]
    assert set(held()) == {("game", "widget-wars"), ("document", "widget-wars/service-manual"), ("mame", "widget-wars"),
                           ("rom_list", "widget-wars")}
    assert held("game")[("game", "widget-wars")].data["title"] == "Widget Wars (older copy)"
    assert library.state().cursor == 4


def test_a_restore_that_dies_half_way_trims_nothing_and_is_detected_again(deck):
    from app.services import library

    run()
    arch = deck["archive"]
    arch.rows = [(s, r) for s, r in arch.rows if s <= 4]
    arch.head = 4
    arch.refuse = True                                    # the rewind's first request is turned away
    out = run()
    assert out["refused"] and len(held()) == 7 and library.state().cursor == 11
    arch.refuse = False
    out = run()
    assert out["restored"] and len(held()) == 4


# --- manuals ---------------------------------------------------------------------------------------------------------------

def test_a_manual_is_fetched_in_ranges_checked_and_kept_by_its_hash(deck):
    from app.services import library_files

    out = run()
    assert out["files"] == 1 and out["files_failed"] == []
    path = library_files.path_for(MANUAL_SHA)
    assert path.read_bytes() == MANUAL and library_files.held(MANUAL_SHA)
    assert not library_files.part_path(MANUAL_SHA).exists()
    files = [(p, h) for p, h in deck["archive"].requests if p.startswith("/api/v1/files/")]
    assert [p for p, _ in files] == [f"/api/v1/files/{MANUAL_SHA}"]
    assert {k.lower(): v for k, v in files[0][1].items()}["range"].startswith("bytes=0-")
    deck["archive"].requests.clear()
    run()
    assert not asks(deck["archive"], "/api/v1/files/")                                 # held: not fetched again


def test_a_rom_image_is_never_asked_for_only_manuals_are(deck):
    run()
    assert {p for p, _ in deck["archive"].requests if p.startswith("/api/v1/files/")} == {f"/api/v1/files/{MANUAL_SHA}"}


def test_a_download_cut_off_part_way_resumes_from_the_byte_it_reached(deck):
    from app.services import library_files

    deck["archive"].cut[MANUAL_SHA] = 100
    out = run()
    assert out["ok"] is False and "cut off" in out["error"] and out["files"] == 0
    assert library_files.part_path(MANUAL_SHA).stat().st_size == 100 and not library_files.held(MANUAL_SHA)
    deck["archive"].requests.clear()
    out = run()
    assert out["ok"] and out["files"] == 1
    assert library_files.path_for(MANUAL_SHA).read_bytes() == MANUAL
    ranges = [{k.lower(): v for k, v in h.items()}["range"] for p, h in deck["archive"].requests if p.startswith("/api/v1/files/")]
    assert ranges and ranges[0].startswith("bytes=100-")


def test_a_part_that_already_holds_the_whole_file_is_checked_and_joined(deck):
    from app.services import library_files

    library_files.part_path(MANUAL_SHA).write_bytes(MANUAL)
    out = run()
    assert out["files"] == 1 and library_files.path_for(MANUAL_SHA).read_bytes() == MANUAL


def test_bytes_that_dont_hash_to_their_name_are_dropped_and_fetched_again_next_run(deck):
    from app.services import library_files

    deck["archive"].lie[MANUAL_SHA] = b"%PDF-1.4\n" + b"x" * 100
    out = run()
    assert out["files"] == 0 and len(out["files_failed"]) == 1 and "hash" in out["files_failed"][0]["why"]
    assert not library_files.held(MANUAL_SHA) and not library_files.part_path(MANUAL_SHA).exists()
    deck["archive"].lie.clear()
    assert run()["files"] == 1


def test_a_file_that_isnt_a_pdf_is_dropped_even_when_its_hash_is_right(deck):
    from app.services import library_files

    body = b"this is a text file, not a PDF"
    sha = hashlib.sha256(body).hexdigest()
    deck["archive"].files[sha] = body
    doc = copy.deepcopy(example("changes-response.json")["records"][1])
    doc["id"], doc["data"]["doc_id"], doc["data"]["sha256"] = "widget-wars/notes", "notes", sha
    deck["archive"].put(doc)
    out = run()
    assert [f["sha256"] for f in out["files_failed"] if "PDF" in f["why"]] == [sha]
    assert not library_files.held(sha) and library_files.held(MANUAL_SHA)


def test_a_manual_the_archive_doesnt_hold_yet_is_waited_for_not_an_error(deck):
    doc = copy.deepcopy(example("changes-response.json")["records"][1])
    doc["id"], doc["data"]["doc_id"], doc["data"]["sha256"] = "widget-wars/later", "later", "ab" * 32
    deck["archive"].put(doc)
    out = run()
    assert out["ok"] and out["files_absent"] == 1 and out["files"] == 1


def test_a_manual_over_the_limit_is_not_kept(deck, app):
    from app.services import library_files

    app.config["LIBRARY_MAX_FILE"] = 100
    out = run()
    assert out["files"] == 0 and "over this application's limit" in out["files_failed"][0]["why"]
    assert not library_files.held(MANUAL_SHA) and not library_files.part_path(MANUAL_SHA).exists()


def test_the_time_budget_leaves_the_rest_for_the_next_run(deck, monkeypatch):
    from app.services import library_files, library_pull

    monkeypatch.setattr(library_pull, "BUDGET", -1.0)
    out = run()
    assert out["ok"] and out["files"] == 0 and out["files_later"] == 1 and out["files_waiting"] == 1
    monkeypatch.undo()
    assert run()["files"] == 1 and library_files.held(MANUAL_SHA)


def test_clearing_a_game_removes_its_records_and_the_manual_only_it_held(deck):
    from app.extensions import db
    from app.services import library, library_files

    run()
    n = library.clear("widget-wars")
    db.session.commit()
    assert n == 7 and held() == {} and not library_files.held(MANUAL_SHA)


# --- out of reach, refused ----------------------------------------------------------------------------------------------

def test_an_archive_out_of_reach_keeps_the_copy_and_says_so(deck, app):
    from app.services import library

    run()
    before = {k: r.rev for k, r in held().items()}
    app.config["LIBRARY_URL"] = "http://127.0.0.1:1"                       # nothing listens there
    out = run()
    assert out["ok"] is False and out["refused"] is False and out["error"]
    assert {k: r.rev for k, r in held().items()} == before
    st = library.state()
    assert st.error and not st.refused and st.last_try is not None
    assert library.refreshed() is not None                                    # the last good read is still the one it shows


def test_a_token_the_archive_refuses_is_the_one_answer_for_a_person(deck, app):
    from app.services import library

    deck["archive"].token = "gbx_a1b2c3d4e5f6.someone-elses"
    out = run()
    assert out["refused"] and not out["ok"] and "refused the token" in out["error"]
    assert library.state().refused is True and held() == {}
    deck["archive"].token = TOKEN
    out = run()
    assert out["ok"] and library.state().refused is False and library.state().error is None


def test_a_feed_that_is_not_the_contracts_shape_is_unreachable_not_a_crash(deck):
    from app.services import library_pull

    class Odd(library_pull.Client):
        def get(self, path, extra=None):
            return 200, b'{"nothing": true}', {}

    with pytest.raises(library_pull.Unreachable):
        Odd("http://x", TOKEN).changes(0, ["widget-wars"])


# --- the command ----------------------------------------------------------------------------------------------------------

def test_the_command_prints_one_line_and_exits_0(deck, app, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("pull_library", os.path.join(os.path.dirname(EXAMPLES), "..", "..", "scripts", "pull_library.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(app, []) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("library: ") and "ok=True" in line and "files=1" in line and "\n" not in line
    assert mod.main(app, ["--status"]) == 0
    assert "7 records held" in capsys.readouterr().out
    deck["archive"].refuse = True
    assert mod.main(app, []) == 1
    app.config.update(LIBRARY_URL="", LIBRARY_TOKEN="")
    assert mod.main(app, []) == 0 and "no archive configured" in capsys.readouterr().out.splitlines()[-1]


# --- the settings ----------------------------------------------------------------------------------------------------------

def _load(**env):
    from app import config

    base = {"SECRET_KEY": "k" * 40, "DATABASE_URL": "sqlite://"}
    return config.load({**base, **env})


def test_settings_default_to_no_library():
    s = _load()
    assert (s.library_url, s.library_token) == ("", "")


def test_settings_accept_an_address_and_a_reader_token():
    s = _load(LIBRARY_ARCHIVE_URL="http://archive.example.test:8081/", LIBRARY_ARCHIVE_TOKEN=TOKEN)
    assert (s.library_url, s.library_token) == ("http://archive.example.test:8081", TOKEN)


@pytest.mark.parametrize("url", ["archive.example.org", "ftp://archive.example.org", "https://archive.example.org/api", "https://a.example.org/?x=1"])
def test_settings_refuse_a_url_that_isnt_just_an_address(url):
    from app import config

    with pytest.raises(config.ConfigError):
        _load(LIBRARY_ARCHIVE_URL=url, LIBRARY_ARCHIVE_TOKEN=TOKEN)


def test_settings_refuse_a_token_that_isnt_a_reader_token():
    from app import config

    with pytest.raises(config.ConfigError):
        _load(LIBRARY_ARCHIVE_URL="https://archive.example.org", LIBRARY_ARCHIVE_TOKEN="hunter2")


def test_half_a_setting_is_no_setting_and_says_so():
    s = _load(LIBRARY_ARCHIVE_URL="https://archive.example.org")
    assert (s.library_url, s.library_token) == ("", "") and any("LIBRARY_ARCHIVE_TOKEN" in w for w in s.warnings)
    s = _load(LIBRARY_ARCHIVE_TOKEN=TOKEN)
    assert s.library_url == "" and any("LIBRARY_ARCHIVE_URL" in w for w in s.warnings)
    s = _load(LIBRARY_ARCHIVE_URL="https://archive.example.org", LIBRARY_ARCHIVE_TOKEN=TOKEN, LIBRARY_ACCESS_CLIENT_ID="only-the-id")
    assert s.library_access_id == "" and any("go together" in w for w in s.warnings)


# --- what a second look at the same records does ---------------------------------------------------------------------------

def test_a_record_read_again_unchanged_is_not_rewritten(deck):
    from app.services import library_pull
    from tests.library_stub import model_of

    run()                                                     # holds widget-wars; the example also has pin-comet-run
    mine = [r for r in copy.deepcopy(example("changes-response-all.json")["records"]) if model_of(r) == "widget-wars"]
    assert library_pull.apply_records(mine) == 0


def test_a_redirect_is_not_followed_with_the_token(deck):
    from tests.library_stub import Server

    other = Server()
    try:
        deck["archive"].redirect = other.url
        out = run()
    finally:
        other.stop()
    assert out["ok"] is False and out["refused"] is False and "instead of answering" in out["error"]
    assert other.archive.requests == [] and held() == {}
