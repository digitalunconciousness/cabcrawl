"""The v1 machine API, against `contract/v1/` as the specification.

The examples in that directory are the normative description of the wire format, because
GATBOX is stdlib-only and neither side carries a JSON Schema validator. So these tests do
not describe the format in their own words — they post `examples/ingest-request.json` and
compare the reply **byte-for-byte** against `examples/ingest-response.json`, then post the
same payload again and compare against `examples/ingest-response-duplicate.json`.

That second comparison is Phase 2's exit criterion: posting the examples twice gives
`created` then `duplicate`.

Every machine here is invented, and the example payloads carry invented machines too — the
contract is committed to a public repository.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(REPO_ROOT, "contract", "v1")
EXAMPLES = os.path.join(CONTRACT, "examples")


def example(name: str):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def device(app):
    """A device, and the machines the example payload refers to."""
    from app.extensions import db
    from app.models import Device, Game

    with app.app_context():
        dev = Device(name="test-bench")
        # The example was generated against this public id, and the uids in it are
        # sha256(public_id|…), so it has to match or every uid would be unreproducible.
        dev.public_id = "a1b2c3d4e5f6"
        token = dev.issue_token()
        db.session.add_all([
            dev,
            Game(name="Widget Wars", barcode="widget-wars",
                 status="Working", location="Floor"),
            Game(name="Sprocket", barcode="pin-sprocket",
                 status="Needs Repair", location="Floor"),
        ])
        db.session.commit()
        return {"token": token, "public_id": dev.public_id, "name": dev.name}


def auth(device):
    return {"Authorization": f"Bearer {device['token']}"}


# ---------------------------------------------------------------------------
# the contract files themselves
# ---------------------------------------------------------------------------

def test_the_contract_matches_its_checksums():
    """Copies of anything drift. GATBOX, which owns it, and the other hub check theirs too."""
    path = os.path.join(REPO_ROOT, "scripts", "contract_checksums.py")
    spec = importlib.util.spec_from_file_location("contract_checksums", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import pathlib

    problems = mod.compare(pathlib.Path(CONTRACT))
    assert not problems, "contract/v1 does not match CHECKSUMS:\n  " + "\n  ".join(problems)


# Where sibling checkouts of the other repositories may be: beside this one, or a cloud
# session's read-only reference clones. CONTRACT_SIBLINGS, a space-separated list of repo
# directories, overrides it.
SIBLINGS = ("gatbox", "Gatbox", "arcadetech-tracker", "ArcadeTech-Tracker", "shop-hub")


def _sibling_contracts():
    """(repo, path) of every sibling checkout's copy of contract v1 that exists."""
    import pathlib

    here = pathlib.Path(CONTRACT).resolve()
    env = os.environ.get("CONTRACT_SIBLINGS")
    parent = pathlib.Path(REPO_ROOT).parent
    repos = ([pathlib.Path(p) for p in env.split()] if env is not None else
             [parent / n for n in SIBLINGS] + [pathlib.Path("/opt/refs") / n for n in SIBLINGS])
    found = []
    for repo in repos:
        for sub in ("docs/contract/v1", "contract/v1"):
            path = repo / sub
            if (path / "CHECKSUMS").is_file() and path.resolve() != here:
                found.append((str(repo), path))
                break
    return found


def _tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


def test_the_contract_is_the_same_as_in_sibling_checkouts():
    """A copy that matches its own CHECKSUMS can still be an older edition than GATBOX's.
    Every sibling checkout found must hold exactly the same files (pull it if it's behind)."""
    import pathlib

    found = _sibling_contracts()
    if not found:
        pytest.skip("no sibling checkout found")
    mine = _tree(pathlib.Path(CONTRACT))
    differ = [repo for repo, path in found if _tree(path) != mine]
    assert not differ, "contract/v1 differs from the copy in: " + ", ".join(differ)


def test_the_example_uids_follow_the_documented_rule():
    """The `.3f` in the reading rule is contractual: `.6f` would duplicate every trace."""
    request = example("ingest-request.json")
    pub = request["device"]
    session = next(i for i in request["items"] if i["kind"] == "rail_session")

    want = hashlib.sha256(
        f"{pub}|rail_session|{session['file']}".encode()
    ).hexdigest()[:32]
    assert session["uid"] == want

    for reading in session["readings"]:
        want = hashlib.sha256(
            f"{pub}|reading|{session['file']}|{reading['epoch']:.3f}".encode()
        ).hexdigest()[:32]
        assert reading["uid"] == want, f"epoch {reading['epoch']} hashes differently"


def test_six_decimal_places_would_be_a_different_uid():
    """Proof the rule above has teeth, rather than passing by coincidence."""
    pub, file_name, epoch = "a1b2c3d4e5f6", "rail_20260925_021402.csv", 1790000000.0
    three = hashlib.sha256(f"{pub}|reading|{file_name}|{epoch:.3f}".encode()).hexdigest()[:32]
    six = hashlib.sha256(f"{pub}|reading|{file_name}|{epoch:.6f}".encode()).hexdigest()[:32]
    assert three != six


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def test_health_needs_no_token(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True and body["contract"] == "v1"
    assert isinstance(body["time"], (int, float))


# ---------------------------------------------------------------------------
# the round trip — Phase 2's exit criterion
# ---------------------------------------------------------------------------

def test_posting_the_example_twice_gives_created_then_duplicate(client, device):
    first = client.post("/api/v1/ingest", json=example("ingest-request.json"),
                        headers=auth(device))
    assert first.status_code == 200, first.get_json()
    assert first.get_json() == example("ingest-response.json")

    second = client.post("/api/v1/ingest", json=example("ingest-request.json"),
                         headers=auth(device))
    assert second.status_code == 200, second.get_json()
    assert second.get_json() == example("ingest-response-duplicate.json")


def test_the_second_post_stores_nothing_new(client, device, app):
    from app.models import MaintenanceRecord, RailSession, Reading

    payload = example("ingest-request.json")
    client.post("/api/v1/ingest", json=payload, headers=auth(device))
    with app.app_context():
        after_one = (RailSession.query.count(), Reading.query.count(),
                     MaintenanceRecord.query.count())
    client.post("/api/v1/ingest", json=payload, headers=auth(device))
    with app.app_context():
        assert (RailSession.query.count(), Reading.query.count(),
                MaintenanceRecord.query.count()) == after_one


def test_the_session_is_stored_as_sent_not_recomputed(client, device, app):
    """The hub records GATBOX's statistics. Deriving its own would be a second opinion."""
    from app.models import RailSession

    sent = next(i for i in example("ingest-request.json")["items"]
                if i["kind"] == "rail_session")
    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    with app.app_context():
        row = RailSession.query.filter_by(uid=sent["uid"]).one()
        assert row.file == sent["file"]
        assert row.powered_mean == sent["powered"]["mean"]
        assert row.in_window_pct == sent["powered"]["in_window_pct"]
        assert row.window_lo == sent["window"]["lo"]
        assert row.alarm_hi == sent["alarm_hi"]
        assert row.verdict == sent["verdict"]["state"]
        assert row.power_cycles == sent["counts"]["power_cycles"]
        assert row.clock == sent["clock"]["source"]
        assert row.game.barcode == sent["machine"]
        assert len(row.readings) == len(sent["readings"])


def test_an_over_range_reading_keeps_its_meter_face(client, device, app):
    """`v` is null for over-range; `raw` and `unit` still say what the meter showed."""
    from app.models import Reading

    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    with app.app_context():
        ol = Reading.query.filter_by(ol=True).one()
        assert ol.v is None
        assert ol.raw == "inf"


def test_the_order_links_to_its_session(client, device, app):
    from app.models import MaintenanceRecord

    sent = next(i for i in example("ingest-request.json")["items"] if i["kind"] == "order")
    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    with app.app_context():
        order = MaintenanceRecord.query.filter_by(external_id=sent["uid"]).one()
        assert order.source == "gatbox"
        assert order.status == "Open"
        assert order.issue_description == sent["issue"]
        assert order.rail_session.uid == sent["rail_session"]


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header", [
    None,
    {"Authorization": ""},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic gbx_a1b2c3d4e5f6.secret"},
    {"Authorization": "Bearer not-a-gatbox-token"},
    {"Authorization": "Bearer gbx_nosuchdevice.secret"},
    {"Authorization": "Bearer gbx_a1b2c3d4e5f6.wrong-secret"},
])
def test_a_bad_token_is_401_json_not_a_redirect(client, device, header):
    """`@login_required` would answer 302 to an HTML login page. A machine needs 401."""
    response = client.post("/api/v1/ingest", json=example("ingest-request.json"),
                           headers=header or {})
    assert response.status_code == 401, response.get_data(as_text=True)
    assert response.get_json()["error"]
    assert "Location" not in response.headers


def test_a_disabled_device_is_refused(client, device, app):
    from app.extensions import db
    from app.models import Device

    with app.app_context():
        dev = Device.query.filter_by(name=device["name"]).one()
        dev.enabled = False
        db.session.commit()
    response = client.post("/api/v1/ingest", json=example("ingest-request.json"),
                           headers=auth(device))
    assert response.status_code == 401


def test_the_refusal_never_says_which_check_failed(client, device):
    """Distinguishing "no such device" from "wrong secret" helps only a guesser."""
    unknown = client.post("/api/v1/ingest", json={},
                          headers={"Authorization": "Bearer gbx_ffffffffffff.x"})
    wrong = client.post("/api/v1/ingest", json={},
                        headers={"Authorization": f"Bearer gbx_{device['public_id']}.x"})
    assert unknown.get_json() == wrong.get_json()


@pytest.mark.parametrize("path", ["/api/v1/roster", "/api/v1/machines/widget-wars/orders"])
def test_the_read_endpoints_need_a_token_too(client, device, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth(device)).status_code == 200


# ---------------------------------------------------------------------------
# what ingest refuses
# ---------------------------------------------------------------------------

def test_a_payload_for_another_device_is_refused(client, device):
    """Its uids are hashed with a different public id, so they could never be reproduced."""
    payload = example("ingest-request.json")
    payload["device"] = "ffffffffffff"
    response = client.post("/api/v1/ingest", json=payload, headers=auth(device))
    assert response.status_code == 400
    assert "different device" in response.get_json()["error"]


@pytest.mark.parametrize("payload,fragment", [
    ([], "JSON object"),
    ({}, "contract must be"),
    ({"contract": "v2"}, "contract must be"),
    ({"contract": "v1", "device": "a1b2c3d4e5f6"}, "items must be"),
    ({"contract": "v1", "device": "a1b2c3d4e5f6", "items": []}, "items must be"),
])
def test_a_malformed_body_is_400(client, device, payload, fragment):
    response = client.post("/api/v1/ingest", json=payload, headers=auth(device))
    assert response.status_code == 400
    assert fragment in response.get_json()["error"]


def test_too_many_items_is_refused_whole(client, device, app):
    from app.models import RailSession

    payload = example("ingest-request.json")
    order = next(i for i in payload["items"] if i["kind"] == "order")
    payload["items"] = [dict(order, uid=f"{n:032x}") for n in range(101)]
    response = client.post("/api/v1/ingest", json=payload, headers=auth(device))
    assert response.status_code == 400
    assert "at most 100 items" in response.get_json()["error"]
    with app.app_context():
        assert RailSession.query.count() == 0, "a refused request must write nothing"


def test_too_many_readings_in_a_session_is_rejected(client, device):
    payload = example("ingest-request.json")
    session = next(i for i in payload["items"] if i["kind"] == "rail_session")
    template = session["readings"][0]
    session["readings"] = [dict(template, uid=f"{n:032x}", epoch=float(n))
                           for n in range(2001)]
    result = client.post("/api/v1/ingest", json=payload,
                         headers=auth(device)).get_json()["results"][0]
    assert result["status"] == "rejected"
    assert "2000 readings per session" in result["reason"]


def test_an_unknown_machine_is_rejected_never_created(client, device, app):
    from app.models import Game

    payload = example("ingest-request.json")
    for item in payload["items"]:
        item["machine"] = "no-such-machine"
    results = client.post("/api/v1/ingest", json=payload,
                          headers=auth(device)).get_json()["results"]
    assert [r["status"] for r in results] == ["rejected", "rejected"]
    assert "no-such-machine" in results[0]["reason"]
    with app.app_context():
        assert Game.query.filter_by(barcode="no-such-machine").first() is None


def test_an_order_naming_an_unsent_session_is_rejected(client, device):
    """Send the session first. Dropping the link quietly would be worse than a retry."""
    payload = example("ingest-request.json")
    payload["items"] = [i for i in payload["items"] if i["kind"] == "order"]
    result = client.post("/api/v1/ingest", json=payload,
                         headers=auth(device)).get_json()["results"][0]
    assert result["status"] == "rejected"
    assert "has not been ingested yet" in result["reason"]


def test_an_unknown_kind_is_rejected_without_affecting_the_others(client, device):
    payload = example("ingest-request.json")
    payload["items"].append({"kind": "telemetry", "uid": "0" * 32})
    results = client.post("/api/v1/ingest", json=payload,
                          headers=auth(device)).get_json()["results"]
    assert [r["status"] for r in results] == ["created", "created", "rejected"]
    assert "telemetry" in results[2]["reason"]


# ---------------------------------------------------------------------------
# roster and orders
# ---------------------------------------------------------------------------

def test_the_roster_lists_machines_with_an_identifier(client, device):
    body = client.get("/api/v1/roster", headers=auth(device)).get_json()
    assert body["contract"] == "v1"
    assert {m["slug"] for m in body["machines"]} == {"widget-wars", "pin-sprocket"}
    shape = example("roster-response.json")["machines"][0]
    assert set(body["machines"][0]) == set(shape)


def test_a_machine_with_no_identifier_is_not_offered(client, device, app):
    """It could not be the subject of an ingest, so listing it would promise a rejection."""
    from app.extensions import db
    from app.models import Game

    with app.app_context():
        db.session.add(Game(name="No Identifier Yet", barcode=None))
        db.session.commit()
    body = client.get("/api/v1/roster", headers=auth(device)).get_json()
    assert "No Identifier Yet" not in {m["name"] for m in body["machines"]}


def test_open_orders_for_a_machine(client, device, app):
    from app.extensions import db
    from app.models import Game, MaintenanceRecord

    with app.app_context():
        game = Game.query.filter_by(barcode="widget-wars").one()
        db.session.add_all([
            MaintenanceRecord(game_id=game.id, issue_description="still open",
                              status="Open", source="web"),
            MaintenanceRecord(game_id=game.id, issue_description="being worked",
                              status="In_Progress", source="web"),
            MaintenanceRecord(game_id=game.id, issue_description="done with",
                              status="Fixed", source="web"),
        ])
        db.session.commit()

    body = client.get("/api/v1/machines/widget-wars/orders",
                      headers=auth(device)).get_json()
    issues = {o["issue"] for o in body["orders"]}
    assert issues == {"still open", "being worked"}, "Fixed is not open"
    assert set(body["orders"][0]) == set(example("orders-response.json")["orders"][0])

    every = client.get("/api/v1/machines/widget-wars/orders?status=all",
                       headers=auth(device)).get_json()
    assert len(every["orders"]) == 3


@pytest.mark.parametrize("query,code", [("?status=open", 200), ("?status=all", 200),
                                        ("?status=closed", 400), ("?status=", 400)])
def test_the_status_filter_is_validated(client, device, query, code):
    assert client.get(f"/api/v1/machines/widget-wars/orders{query}",
                      headers=auth(device)).status_code == code


def test_orders_for_an_unknown_machine_is_404(client, device):
    response = client.get("/api/v1/machines/nope/orders", headers=auth(device))
    assert response.status_code == 404
    assert "nope" in response.get_json()["error"]


def test_an_epoch_becomes_a_naive_utc_datetime():
    """Naive, holding UTC -- and this is asserted on the function, not on a stored row.

    These columns are TIMESTAMP WITHOUT TIME ZONE. Handing psycopg2 an *aware* datetime makes
    it convert to the database session's own zone and drop the offset, so 09:00 UTC lands as
    04:00 on a server set to CDT and every page then labels that "UTC" -- silently right on a
    UTC server, wrong on any other.

    The suite runs on SQLite (see conftest), which does no such conversion, so a round-trip
    assertion here would pass whether or not the bug were present. gdd-integration's
    verify-sync.sh asserts the stored value against real PostgreSQL; this pins the contract
    of the conversion itself, which is what the storage depends on.
    """
    from datetime import datetime, timezone

    from app.routes.api_v1 import _when

    got = _when(1790000000.0)
    assert got.tzinfo is None, "aware: psycopg2 would shift it to the server's zone"
    assert got == datetime.fromtimestamp(1790000000.0, timezone.utc).replace(tzinfo=None)
    assert _when(None) is None and _when("nope") is None


def test_the_device_records_when_it_was_last_seen(client, device, app):
    from app.models import Device

    with app.app_context():
        assert Device.query.filter_by(name=device["name"]).one().last_seen is None
    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    with app.app_context():
        assert Device.query.filter_by(name=device["name"]).one().last_seen is not None


# ---------------------------------------------------------------------------
# session_tag: a trace attached to an order that already exists
# ---------------------------------------------------------------------------

def _tagged(client, device, app, **overrides):
    """Post the example payload (so a session and an order exist), then a session_tag
    naming that order and a second session. Returns (response item, order id)."""
    from app.extensions import db
    from app.models import MaintenanceRecord, RailSession

    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    sent = next(i for i in example("ingest-request.json")["items"] if i["kind"] == "order")
    with app.app_context():
        order_id = MaintenanceRecord.query.filter_by(external_id=sent["uid"]).one().id
        # A second session for the same machine, ingested the ordinary way.
        session = next(i for i in example("ingest-request.json")["items"]
                       if i["kind"] == "rail_session")
        second = dict(session, uid="b" * 32, file="rail_20261006_030303.csv", readings=[])
    client.post("/api/v1/ingest", json={"contract": "v1", "device": device["public_id"], "items": [second]},
                headers=auth(device))

    item = {"kind": "session_tag", "uid": "c" * 32, "order": order_id,
            "rail_session": "b" * 32, "created": 1790000500.0, "note": "second pass, cold"}
    item.update(overrides)
    body = client.post("/api/v1/ingest",
                       json={"contract": "v1", "device": device["public_id"], "items": [item]},
                       headers=auth(device)).get_json()
    return body["results"][0], order_id


def test_a_session_tag_attaches_a_second_trace(client, device, app):
    from app.extensions import db
    from app.models import MaintenanceRecord

    result, order_id = _tagged(client, device, app)
    assert result["status"] == "created", result
    with app.app_context():
        order = db.session.get(MaintenanceRecord, order_id)
        assert [t.rail_session.uid for t in order.session_tags] == ["b" * 32]
        assert order.session_tags[0].note == "second pass, cold"
        assert order.session_tags[0].source == "gatbox"


def test_the_tag_leaves_the_prompting_session_alone(client, device, app):
    """rail_session_id means "why does this order exist" and a tag must not overwrite it."""
    from app.extensions import db
    from app.models import MaintenanceRecord

    sent = next(i for i in example("ingest-request.json")["items"] if i["kind"] == "order")
    _result, order_id = _tagged(client, device, app)
    with app.app_context():
        order = db.session.get(MaintenanceRecord, order_id)
        assert order.rail_session.uid == sent["rail_session"]


def test_the_same_tag_twice_is_duplicate(client, device, app):
    """Unlike an order, a tag has a natural key -- the (order, session) pair -- so that is
    what the hub dedupes on rather than the uid."""
    from app.models import RailSessionTag

    _tagged(client, device, app)
    result, _order_id = _tagged(client, device, app)
    assert result["status"] == "duplicate", result
    with app.app_context():
        assert RailSessionTag.query.count() == 1


def test_a_tag_with_a_different_uid_for_the_same_pair_is_still_duplicate(client, device, app):
    """The pair is the identity. A re-minted uid must not produce a second row."""
    from app.models import RailSessionTag

    _tagged(client, device, app)
    result, _ = _tagged(client, device, app, uid="d" * 32)
    assert result["status"] == "duplicate", result
    with app.app_context():
        assert RailSessionTag.query.count() == 1


def test_a_tag_for_an_unknown_order_is_rejected(client, device, app):
    from app.models import RailSessionTag

    result, _ = _tagged(client, device, app, order=999999)
    assert result["status"] == "rejected"
    assert "order" in result["reason"]
    with app.app_context():
        assert RailSessionTag.query.count() == 0


def test_a_tag_for_an_unsent_session_is_rejected(client, device, app):
    """The same rule as an order naming an unsent session: refuse rather than drop the link.
    This one clears on a retry once the session has been pushed."""
    from app.models import RailSessionTag

    result, _ = _tagged(client, device, app, rail_session="f" * 32)
    assert result["status"] == "rejected"
    assert "rail_session" in result["reason"]
    with app.app_context():
        assert RailSessionTag.query.count() == 0


def test_a_tag_on_a_closed_order_is_accepted(client, device, app):
    """**Accepted, not rejected.** GATBOX tags from a cache that can be minutes stale, and
    gatbox-sync retries a rejected item -- so refusing a closed order would be a retry loop
    that can never clear. The evidence is still worth having on the record."""
    from app.extensions import db
    from app.models import MaintenanceRecord, RailSessionTag

    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    sent = next(i for i in example("ingest-request.json")["items"] if i["kind"] == "order")
    with app.app_context():
        order = MaintenanceRecord.query.filter_by(external_id=sent["uid"]).one()
        order.status = "Closed"
        db.session.commit()

    result, _ = _tagged(client, device, app)
    assert result["status"] == "created", result
    with app.app_context():
        assert RailSessionTag.query.count() == 1


def test_a_tag_whose_session_is_for_another_machine_is_rejected(client, device, app):
    """Almost certainly a bug on the Pi, and silently filing one machine's trace against
    another machine's order would be worse than a refusal."""
    from app.extensions import db
    from app.models import Game, RailSession, RailSessionTag

    client.post("/api/v1/ingest", json=example("ingest-request.json"),
                headers=auth(device))
    sent = next(i for i in example("ingest-request.json")["items"] if i["kind"] == "order")
    session = next(i for i in example("ingest-request.json")["items"]
                   if i["kind"] == "rail_session")
    other = dict(session, uid="e" * 32, file="rail_20261006_040404.csv",
                 machine="pin-sprocket", readings=[])
    client.post("/api/v1/ingest", json={"contract": "v1", "device": device["public_id"], "items": [other]},
                headers=auth(device))
    from app.models import MaintenanceRecord
    with app.app_context():
        order_id = MaintenanceRecord.query.filter_by(external_id=sent["uid"]).one().id

    item = {"kind": "session_tag", "uid": "c" * 32, "order": order_id,
            "rail_session": "e" * 32, "created": 1790000500.0}
    body = client.post("/api/v1/ingest",
                       json={"contract": "v1", "device": device["public_id"], "items": [item]},
                       headers=auth(device)).get_json()
    assert body["results"][0]["status"] == "rejected"
    assert "machine" in body["results"][0]["reason"]
    with app.app_context():
        assert RailSessionTag.query.count() == 0


def test_a_tag_needs_a_rail_session(client, device, app):
    result, _ = _tagged(client, device, app, rail_session=None)
    assert result["status"] == "rejected"


def test_the_session_tag_example_matches_what_the_hub_reads(client, device, app):
    """The example is illustrative -- `order` is a hub-assigned id, so it cannot be fixed in
    advance the way the session uids can. Its *shape* is still the specification."""
    illustrative = example("session-tag-request.json")
    item = next(i for i in illustrative["items"] if i["kind"] == "session_tag")
    assert set(item) == {"kind", "uid", "order", "rail_session", "created", "note"}
    assert isinstance(item["order"], int)
    assert len(item["uid"]) == 32


def test_the_session_tag_example_uid_follows_the_documented_rule():
    illustrative = example("session-tag-request.json")
    pub = illustrative["device"]
    item = next(i for i in illustrative["items"] if i["kind"] == "session_tag")
    want = hashlib.sha256(
        f"{pub}|session_tag|{item['order']}|{item['rail_session']}".encode()
    ).hexdigest()[:32]
    assert item["uid"] == want
