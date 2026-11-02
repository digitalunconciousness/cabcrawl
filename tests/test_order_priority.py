"""An order's priority from the device API, and an order that already holds "Urgent"
(owner's fix, 2026-10-28).

GATBOX's deck offers Low, Medium, High, Critical and Urgent; this app's orders know the first
four. The ingest stored whatever it was sent. Now the five are accepted, Urgent is stored as
Critical, a missing priority is Medium as before, and anything else rejects that item while
the rest of the batch goes through. A row that already holds "Urgent" sorts, shows and edits
as Critical. Invented data throughout; the contract examples are posted unchanged apart from
the one field under test.
"""
from __future__ import annotations

import copy
import os
import sys

import pytest
from test_api_v1_contract import auth, example

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "characterization"))
from char_support import login
from seed import seed_floor

ORDER = 1          # the order's index in contract/v1/examples/ingest-request.json


@pytest.fixture()
def bench(app):
    """The contract tests' device and the two machines its example names."""
    from app.extensions import db
    from app.models import Device, Game

    dev = Device(name="test-bench")
    dev.public_id = "a1b2c3d4e5f6"
    token = dev.issue_token()
    db.session.add_all([dev, Game(name="Widget Wars", barcode="widget-wars", status="Working",
                                  location="Floor"),
                        Game(name="Sprocket", barcode="pin-sprocket", status="Working",
                             location="Floor")])
    db.session.commit()
    return {"token": token, "public_id": dev.public_id, "name": dev.name}


def ingest(client, bench, payload):
    return client.post("/api/v1/ingest", json=payload, headers=auth(bench))


def with_priority(value, *, drop=False):
    payload = copy.deepcopy(example("ingest-request.json"))
    if drop:
        payload["items"][ORDER].pop("priority", None)
    else:
        payload["items"][ORDER]["priority"] = value
    return payload


def stored():
    from app.models import MaintenanceRecord

    order = MaintenanceRecord.query.filter_by(source="gatbox").one_or_none()
    return order.priority if order else None


# --- the device API -----------------------------------------------------------------------------

@pytest.mark.parametrize(("sent", "kept"), [
    ("Low", "Low"), ("Medium", "Medium"), ("High", "High"), ("Critical", "Critical"),
    ("Urgent", "Critical"),
])
def test_the_five_priorities_are_accepted_and_urgent_is_stored_as_critical(client, bench,
                                                                           sent, kept):
    resp = ingest(client, bench, with_priority(sent))
    assert resp.status_code == 200
    assert [r["status"] for r in resp.get_json()["results"]] == ["created", "created"]
    assert stored() == kept


@pytest.mark.parametrize("payload", [with_priority(None, drop=True), with_priority(None),
                                     with_priority("")],
                         ids=["left out", "null", "empty"])
def test_a_missing_priority_is_medium(client, bench, payload):
    resp = ingest(client, bench, payload)
    assert [r["status"] for r in resp.get_json()["results"]] == ["created", "created"]
    assert stored() == "Medium"


@pytest.mark.parametrize("value", ["urgent", "Highest", "P1", " High", 3, ["High"],
                                   {"level": "High"}, True])
def test_any_other_priority_rejects_the_item_and_the_rest_goes_through(client, bench, value):
    from app.models import RailSession

    resp = ingest(client, bench, with_priority(value))
    assert resp.status_code == 200
    results = resp.get_json()["results"]
    assert results[0]["status"] == "created"                        # the session went in
    assert results[ORDER]["status"] == "rejected"
    assert results[ORDER]["reason"] == (
        "priority must be one of Low, Medium, High, Critical, Urgent (or left out)")
    assert RailSession.query.count() == 1 and stored() is None


def test_a_rejected_order_can_be_sent_again_corrected(client, bench):
    ingest(client, bench, with_priority("Whenever"))
    resp = ingest(client, bench, with_priority("Urgent"))
    assert [r["status"] for r in resp.get_json()["results"]] == ["duplicate", "created"]
    assert stored() == "Critical"


# --- an order already holding "Urgent" ----------------------------------------------------------

@pytest.fixture()
def floor(app):
    return seed_floor()


@pytest.fixture()
def urgent(app, floor):
    """An order stored before this fix, with the deck's "Urgent" in it."""
    from app.extensions import db
    from app.models import MaintenanceRecord

    order = MaintenanceRecord(game_id=floor["raider"], issue_description="Coin mech jammed",
                              status="Open", priority="Urgent", source="gatbox")
    db.session.add(order)
    db.session.commit()
    return order.id


def test_an_urgent_order_sorts_with_the_critical_ones(app, floor, urgent):
    from app.services.work_orders import PRIORITY_RANK, list_orders, priority_level

    assert PRIORITY_RANK["Urgent"] == PRIORITY_RANK["Critical"] == 0
    assert priority_level("Urgent") == "Critical" and priority_level("High") == "High"
    assert priority_level(None) is None
    orders = list_orders(tab="open", sort="priority").orders
    assert orders[0].id == urgent


def test_an_urgent_order_tops_the_dashboard_list(app, floor, urgent):
    from app.services.dashboard import PRIORITY_ORDER

    assert PRIORITY_ORDER.get("Urgent") == 0


def test_an_urgent_order_is_shown_as_critical(client, floor, urgent):
    login(client, "manager")
    page = client.get(f"/maintenance_detail/{urgent}").get_data(as_text=True)
    head = page[:page.index("Coin mech jammed")]
    assert "Critical" in head and "Urgent" not in head
    queue = client.get("/maintenance_orders").get_data(as_text=True)
    row = queue[queue.index("Coin mech jammed"):][:600]
    assert "Critical" in row and "Urgent" not in row


def test_editing_an_urgent_order_keeps_it_critical_not_low(client, floor, urgent):
    """The edit form had no "Urgent" option, so the browser picked the first one, Low, and
    saving any other change quietly demoted the order."""
    from app.extensions import db
    from app.models import MaintenanceRecord

    login(client, "operator")
    form = client.get(f"/update_maintenance/{urgent}").get_data(as_text=True)
    assert '<option value="Critical" selected>' in form
    client.post(f"/update_maintenance/{urgent}", data={
        "status": "In_Progress", "priority": "Critical", "fix_description": "",
        "technician": "Tech A"})
    db.session.expire_all()
    assert db.session.get(MaintenanceRecord, urgent).priority == "Critical"
