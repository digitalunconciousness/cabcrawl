"""Work orders: step 4.4 of the redesign (list, forms, detail, log work, photos, PDF)."""
from __future__ import annotations

import io

import pytest

from char_support import flashes, login
from test_routes_post import fresh, png_bytes


def get(client, role, url):
    client.post("/logout")
    login(client, role)
    resp = client.get(url)
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


# --- list -----------------------------------------------------------------------------------

def test_tabs_split_open_from_closed(client, floor):
    body = get(client, "readonly", "/maintenance_orders")
    assert "Joystick drifts left" in body and "Coin mech jammed" not in body
    body = get(client, "readonly", "/maintenance_orders?tab=closed")
    assert "Coin mech jammed" in body and "Joystick drifts left" not in body
    body = get(client, "readonly", "/maintenance_orders?tab=all")
    assert "Coin mech jammed" in body and "Joystick drifts left" in body


def test_search_reaches_the_machine_name_and_the_place(client, floor):
    body = get(client, "readonly", "/maintenance_orders?tab=all&search=raider")
    assert "Joystick drifts left" in body and "Ceiling light flickers" not in body
    body = get(client, "readonly", "/maintenance_orders?search=back+bar")
    assert "Ceiling light flickers" in body


def test_open_orders_sort_by_priority_by_default(client, floor):
    body = get(client, "readonly", "/maintenance_orders")
    assert body.index("Joystick drifts left") < body.index("Ceiling light flickers")  # High first


def test_coin_door_and_bench_orders_are_badged(client, floor):
    from app.extensions import db
    from app.models import MaintenanceRecord

    fresh(MaintenanceRecord, floor["open_order"]).source = "coindoor"
    db.session.commit()
    assert "coin door" in get(client, "readonly", "/maintenance_orders")


# --- detail ---------------------------------------------------------------------------------

def test_the_detail_page_offers_each_role_its_actions(client, floor):
    url = f"/maintenance_detail/{floor['open_order']}"
    body = get(client, "readonly", url)
    assert "Reseated the harness" in body and "Close this order" not in body
    assert "/delete_maintenance/" not in body
    body = get(client, "operator", url)
    assert "Close this order" in body and "Log work" in body and "/delete_maintenance/" not in body
    assert "/delete_maintenance/" in get(client, "manager", url)


def test_closing_from_the_detail_page(client, floor):
    from app.models import MaintenanceRecord

    login(client, "operator")
    resp = client.post(f"/close_maintenance/{floor['open_order']}", data={
        "status": "Fixed", "fix_description": "New microswitch", "cost": "6.50",
        "technician": "Tech A"})
    assert resp.headers["Location"] == f"/maintenance_detail/{floor['open_order']}"
    order = fresh(MaintenanceRecord, floor["open_order"])
    assert (order.status, order.cost, order.fix_description) == ("Fixed", 6.5, "New microswitch")


@pytest.mark.parametrize("data", [{"status": "Open"}, {"status": "Fixed", "cost": "lots"}])
def test_closing_validates_on_the_server(client, floor, data):
    from app.models import MaintenanceRecord

    login(client, "operator")
    client.post(f"/close_maintenance/{floor['open_order']}", data=data)
    assert fresh(MaintenanceRecord, floor["open_order"]).status == "Open"
    assert any(cat == "error" for cat, _ in flashes(client))


# --- log work (update) ----------------------------------------------------------------------

@pytest.mark.parametrize(("field", "value", "message"), [
    ("time_spent", "an hour", "Time spent must be a number"),
    ("work_cost", "-3", "cannot be negative"),
    ("status", "Exploded", "Pick a status"),
])
def test_log_work_validates_and_saves_nothing(client, floor, field, value, message):
    from app.models import WorkLog

    login(client, "operator")
    data = {"status": "In_Progress", "work_notes": "Checked the harness", field: value}
    resp = client.post(f"/update_maintenance/{floor['open_order']}", data=data)
    assert resp.status_code == 400 and message in resp.get_data(as_text=True)
    assert WorkLog.query.filter_by(maintenance_id=floor["open_order"]).count() == 1
    assert "Checked the harness" in resp.get_data(as_text=True), "what was typed survives"


def test_a_part_row_without_a_valid_quantity_is_an_error(client, floor):
    login(client, "operator")
    resp = client.post(f"/update_maintenance/{floor['open_order']}", data={
        "status": "Open", "inventory_item_0": str(floor["belt"]), "inventory_quantity_0": "two",
        "item_action_0": "use"})
    assert resp.status_code == 400 and "whole number" in resp.get_data(as_text=True)


# --- create ---------------------------------------------------------------------------------

def test_the_full_form_keeps_priority(client, floor):
    from app.models import MaintenanceRecord

    login(client, "operator")
    client.post(f"/maintenance/game/{floor['raider']}", data={
        "issue_description": "Marquee light out", "status": "Open", "priority": "Low",
        "machine_down": "no"})
    assert MaintenanceRecord.query.filter_by(issue_description="Marquee light out").one().priority == "Low"


def test_an_invalid_create_is_a_400_with_the_message(client, floor):
    login(client, "operator")
    resp = client.post("/maintenance/general", data={"status": "Open", "issue_description": ""})
    assert resp.status_code == 400 and "field is required" in resp.get_data(as_text=True)


# --- photos ---------------------------------------------------------------------------------

def test_a_photo_that_is_not_an_image_is_not_attached(client, floor, sandbox):
    """F-8, fixed: an upload Pillow cannot read is refused instead of attached broken."""
    from app.models import MaintenanceRecord

    login(client, "operator")
    client.post(f"/maintenance_photos/{floor['open_order']}", data={
        "photos": [(io.BytesIO(b"not an image"), "fake.png")]},
        content_type="multipart/form-data")
    assert fresh(MaintenanceRecord, floor["open_order"]).get_photos() == []
    assert any("could not be read" in m for _, m in flashes(client))


def test_photos_are_named_for_what_they_are(client, floor, sandbox):
    from app.models import MaintenanceRecord

    login(client, "operator")
    client.post(f"/maintenance_photos/{floor['open_order']}", data={
        "photos": [(io.BytesIO(png_bytes()), "cab.png")]}, content_type="multipart/form-data")
    [name] = fresh(MaintenanceRecord, floor["open_order"]).get_photos()
    assert name.endswith(".jpg")


def test_deleting_a_photo_the_order_does_not_hold_is_refused(client, floor, sandbox):
    """F-18 (photos): the route used to delete any filename it was given."""
    other = sandbox / "uploads" / "maintenance_photos" / "someone_elses.jpg"
    other.write_bytes(b"x")
    login(client, "manager")
    client.post(f"/delete_maintenance_photo/{floor['open_order']}/someone_elses.jpg")
    assert other.exists()
    assert ("error", "That photo is not on this work order.") in flashes(client)


def test_the_s3_copy_runs_for_each_saved_photo(app, client, floor, sandbox, monkeypatch):
    """USE_CLOUD_STORAGE is kept: each saved photo is also handed to the S3 copy."""
    from app.routes import maintenance

    copied = []
    app.config["CLOUD_PHOTOS"] = True               # USE_CLOUD_STORAGE, read at startup
    monkeypatch.setattr(maintenance, "_copy_to_cloud", lambda path, name: copied.append(name))
    login(client, "operator")
    client.post(f"/maintenance_photos/{floor['open_order']}", data={
        "photos": [(io.BytesIO(png_bytes()), "a.png"), (io.BytesIO(png_bytes()), "b.png")]},
        content_type="multipart/form-data")
    assert len(copied) == 2


# --- PDF ------------------------------------------------------------------------------------

def test_a_work_order_pdf_survives_markup_characters(client, floor):
    """F-12, fixed: user text is escaped before ReportLab parses it as markup."""
    from app.extensions import db
    from app.models import MaintenanceRecord

    fresh(MaintenanceRecord, floor["open_order"]).issue_description = "Loose wire, see <b>J3"
    db.session.commit()
    login(client, "readonly")
    resp = client.get(f"/download_maintenance_record/{floor['open_order']}")
    assert resp.status_code == 200 and resp.data.startswith(b"%PDF")
