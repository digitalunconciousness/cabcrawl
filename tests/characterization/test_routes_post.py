"""Every POST (and the GET-with-side-effects auth routes): what each one writes.

Characterization of today's behaviour, including its rough edges. Known bugs are pinned as
they are, with the fix described by a strict xfail in ``test_flags.py``.

CSRF is off in these tests (``WTF_CSRF_ENABLED=False``, set by ``tests/conftest.py``). The
CSRF work in Step 5 adds its own tests that every POST refuses a missing token.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time

import pytest

from char_support import LEVEL, PASSWORD, flashes, login


def fresh(model, ident):
    """Re-read a row after a request, past the test's own identity map."""
    from app.extensions import db

    db.session.expire_all()
    return db.session.get(model, ident)


def png_bytes(size=(8, 8), color=(255, 46, 159)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------------------
# Role refusals: one request per POST route, from the role just below its minimum.
# ---------------------------------------------------------------------------------------

POSTS = [
    # (url builder, minimum role)
    (lambda f: "/add_game", "operator"),
    (lambda f: f"/edit_game/{f['raider']}", "operator"),
    (lambda f: f"/record_plays/{f['raider']}", "operator"),
    (lambda f: "/delete_play_record/1", "manager"),
    (lambda f: f"/round/{f['raider']}", "operator"),
    (lambda f: "/round/end", "operator"),
    (lambda f: "/rotation/swap", "manager"),
    (lambda f: f"/rails/session/{f['session_uid']}/attach", "operator"),
    (lambda f: "/inventory/places/new", "operator"),
    (lambda f: f"/inventory/places/{f['shelf']}/edit", "operator"),
    (lambda f: f"/inventory/places/{f['shelf']}/archive", "manager"),
    (lambda f: f"/inventory/places/{f['shelf']}/delete", "manager"),
    (lambda f: f"/inventory/places/{f['shelf']}/count", "operator"),
    (lambda f: f"/inventory/places/{f['shelf']}/count/add", "operator"),
    (lambda f: "/inventory/categories/new", "manager"),
    (lambda f: f"/inventory/categories/{f['category']}/edit", "manager"),
    (lambda f: f"/inventory/categories/{f['category']}/delete", "manager"),
    (lambda f: "/inventory/suppliers/new", "manager"),
    (lambda f: f"/inventory/suppliers/{f['supplier']}/edit", "manager"),
    (lambda f: f"/inventory/suppliers/{f['supplier']}/archive", "manager"),
    (lambda f: f"/inventory/{f['belt']}/suppliers/new", "manager"),
    (lambda f: f"/inventory/{f['belt']}/suppliers/{f['supplier_link']}/edit", "manager"),
    (lambda f: f"/inventory/{f['belt']}/suppliers/{f['supplier_link']}/prefer", "manager"),
    (lambda f: f"/inventory/{f['belt']}/suppliers/{f['supplier_link']}/remove", "manager"),
    (lambda f: f"/inventory/{f['belt']}/photo", "operator"),
    (lambda f: f"/inventory/{f['belt']}/photo/delete", "operator"),
    (lambda f: "/inventory/shopping/request", "manager"),
    (lambda f: "/inventory/shopping/order", "manager"),
    (lambda f: "/inventory/shopping/settings", "manager"),
    (lambda f: f"/inventory/requests/{f['request']}/receive", "operator"),
    (lambda f: "/inventory/orders/new", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/edit", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/lines/new", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/lines/{f['order_line']}/edit", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/lines/{f['order_line']}/remove", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/send", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/cancel", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/close", "manager"),
    (lambda f: f"/inventory/orders/{f['order']}/lines/{f['order_line']}/receive", "operator"),
    (lambda f: f"/inventory/{f['cpu']}/units/new", "operator"),
    (lambda f: f"/inventory/units/{f['unit']}/move", "operator"),
    (lambda f: f"/inventory/units/{f['unit']}/install", "operator"),
    (lambda f: f"/inventory/units/{f['unit_in_game']}/remove", "operator"),
    (lambda f: f"/inventory/units/{f['unit']}/repair", "operator"),
    (lambda f: f"/inventory/units/{f['unit']}/back", "operator"),
    (lambda f: f"/inventory/units/{f['unit']}/retire", "operator"),
    (lambda f: f"/inventory/units/{f['unit']}/edit", "manager"),
    (lambda f: "/inventory/counts/new", "manager"),
    (lambda f: f"/inventory/counts/{f['stock_count']}/save", "operator"),
    (lambda f: f"/inventory/counts/{f['stock_count']}/found", "operator"),
    (lambda f: f"/inventory/counts/{f['stock_count']}/apply", "manager"),
    (lambda f: f"/inventory/counts/{f['stock_count']}/cancel", "manager"),
    (lambda f: f"/maintenance/{f['open_order']}/reservations/{f['reservation']}/use",
     "operator"),
    (lambda f: f"/maintenance/{f['open_order']}/reservations/{f['reservation']}/release",
     "operator"),
    (lambda f: "/maintenance/tasks/new", "manager"),
    (lambda f: f"/maintenance/tasks/{f['task']}/edit", "manager"),
    (lambda f: f"/maintenance/tasks/{f['task']}/delete", "manager"),
    (lambda f: "/admin/alerts/new", "admin"),
    (lambda f: f"/admin/alerts/{f['channel']}/edit", "admin"),
    (lambda f: f"/admin/alerts/{f['channel']}/delete", "admin"),
    (lambda f: f"/admin/alerts/{f['channel']}/test", "admin"),
    (lambda f: "/admin/alerts/hours", "admin"),
    (lambda f: "/admin/branding", "admin"),
    (lambda f: f"/admin/devices/{f['device']}", "admin"),
    (lambda f: f"/add_baseline/{f['courier']}", "operator"),
    (lambda f: f"/delete_game/{f['pinball']}", "admin"),
    (lambda f: "/bulk_update_games", "manager"),
    (lambda f: "/import_games", "manager"),
    (lambda f: f"/maintenance/game/{f['raider']}", "operator"),
    (lambda f: "/maintenance/general", "operator"),
    (lambda f: f"/update_maintenance/{f['open_order']}", "operator"),
    (lambda f: f"/close_maintenance/{f['open_order']}", "operator"),
    (lambda f: f"/delete_maintenance/{f['closed_order']}", "manager"),
    (lambda f: f"/maintenance_photos/{f['open_order']}", "operator"),
    (lambda f: f"/delete_maintenance_photo/{f['open_order']}/x.jpg", "manager"),
    (lambda f: "/inventory/add", "manager"),
    (lambda f: f"/inventory/{f['belt']}/edit", "manager"),
    (lambda f: f"/inventory/{f['belt']}/adjust_stock", "operator"),
    (lambda f: f"/inventory/{f['belt']}/delete", "admin"),
    (lambda f: f"/inventory/resolve_alert/{f['alert']}", "manager"),
    (lambda f: "/inventory/request", "operator"),
    (lambda f: f"/inventory/requests/{f['request']}/update", "manager"),
    (lambda f: f"/inventory/requests/{f['request']}/delete", "operator"),
    (lambda f: f"/inventory/requests/{f['request']}/update_tracking", "manager"),
    (lambda f: "/admin/users", "admin"),
    (lambda f: "/admin/create_user", "admin"),
    # Admin since 4.7c, like the storage page it is on (managers had no page offering it).
    (lambda f: "/admin/cleanup_photos", "admin"),
    (lambda f: "/create_backup", "admin"),
    (lambda f: "/restore_backup", "admin"),
    (lambda f: "/delete_backup", "admin"),
    # Coin-door labels are keys: printing (which mints) and retiring them is managers' (F-17).
    (lambda f: f"/game/{f['raider']}/report-token/rotate", "manager"),
    (lambda f: "/labels/coindoor/sheet", "manager"),
    (lambda f: "/logout", "readonly"),
]
BELOW = {"readonly": "anon", "operator": "readonly", "manager": "operator", "admin": "manager"}


@pytest.mark.parametrize(("build", "minimum"), POSTS, ids=[f"{m}-{i}" for i, (_, m) in
                                                            enumerate(POSTS)])
def test_a_post_refuses_the_role_below_its_minimum(client, floor, build, minimum):
    role = BELOW[minimum]
    login(client, role)
    resp = client.post(build(floor), data={})
    assert resp.status_code == 302
    if role == "anon":
        assert "/login" in resp.headers["Location"]
    else:
        assert resp.headers["Location"].endswith("/")
        assert ("danger", "You do not have permission to access this page.") in flashes(client)


def test_every_post_route_is_listed(app, floor):
    """A new POST route must be added to POSTS (or to the documented exceptions)."""
    adapter = app.url_map.bind("localhost")
    covered = set()
    for build, _ in POSTS:
        endpoint, _args = adapter.match(build(floor), method="POST")
        covered.add(endpoint)
    exempt = {"auth.login", "auth.setup", "auth.profile", "auth.change_password",
              "report.report_form", "api_v1.ingest"}
    missing = sorted(
        r.endpoint for r in app.url_map.iter_rules()
        if "POST" in r.methods and r.endpoint not in covered | exempt
    )
    assert missing == []


# ---------------------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------------------

class TestAuth:
    def test_login_is_case_insensitive_and_lands_on_the_dashboard(self, client, floor):
        resp = client.post("/login", data={"username": "TEST-Manager", "password": PASSWORD})
        assert resp.status_code == 302 and resp.headers["Location"] == "/"

    def test_a_wrong_password_is_refused_with_a_flash(self, client, floor):
        resp = client.post("/login", data={"username": "test-manager", "password": "nope"})
        assert resp.status_code == 200
        assert "Invalid username or password" in resp.get_data(as_text=True)

    def test_five_failures_lock_the_account(self, client, floor):
        for _ in range(5):
            client.post("/login", data={"username": "test-manager", "password": "nope"})
        resp = client.post("/login", data={"username": "test-manager", "password": PASSWORD})
        assert resp.status_code == 200
        assert "locked" in resp.get_data(as_text=True)

    def test_an_inactive_user_cannot_log_in(self, client, floor):
        from app.extensions import db
        from app.models import User

        fresh(User, floor["users"]["operator"]).is_active = False
        db.session.commit()
        resp = client.post("/login", data={"username": "test-operator", "password": PASSWORD})
        assert resp.status_code == 200

    def test_a_safe_next_is_followed_and_an_offsite_one_is_not(self, client, floor):
        resp = client.post("/login?next=/games",
                           data={"username": "test-manager", "password": PASSWORD})
        assert resp.headers["Location"] == "/games"
        client.post("/logout")
        resp = client.post("/login?next=https://evil.example/",
                           data={"username": "test-manager", "password": PASSWORD})
        assert resp.headers["Location"] == "/"

    def test_must_change_password_sends_you_to_change_it(self, client, floor):
        from app.extensions import db
        from app.models import User

        fresh(User, floor["users"]["operator"]).must_change_password = True
        db.session.commit()
        resp = client.post("/login", data={"username": "test-operator", "password": PASSWORD})
        assert resp.headers["Location"] == "/change_password"

    def test_logout_is_a_post_and_ends_the_session(self, client, floor):
        """F-21: a GET only asks, so a link or a prefetch cannot sign anyone out."""
        login(client, "manager")
        assert client.get("/logout").status_code == 200
        assert client.get("/games").status_code == 200
        resp = client.post("/logout")
        assert resp.headers["Location"] == "/login"
        assert "/login" in client.get("/games").headers["Location"]

    def test_setup_is_closed_once_any_user_exists(self, client, floor):
        resp = client.post("/setup", data={"username": "intruder", "password": "x"})
        assert resp.headers["Location"] == "/login"

    def test_setup_page_opens_on_an_empty_database(self, client, app):
        """F-36, fixed in the base-shell step: this was a 500 on every fresh install."""
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert 'name="confirm"' in resp.get_data(as_text=True)

    def test_setup_creates_the_first_admin(self, client, app):
        from app.models import User

        resp = client.post("/setup", data={"username": "first-admin",
                                           "password": "Synthetic-Passw0rd!",
                                           "confirm": "Synthetic-Passw0rd!"})
        assert resp.headers["Location"] == "/login"
        user = User.query.filter_by(username="first-admin").one()
        assert user.role == "admin" and user.must_change_password is False

    @pytest.mark.parametrize(("data", "message"), [
        ({"username": "ab", "password": "Synthetic-Passw0rd!", "confirm": "Synthetic-Passw0rd!"},
         "at least 3 characters"),
        ({"username": "first-admin", "password": "short", "confirm": "short"},
         "at least 8 characters"),
        ({"username": "first-admin", "password": "alllowercase1", "confirm": "alllowercase1"},
         "uppercase, lowercase"),
        ({"username": "first-admin", "password": "Synthetic-Passw0rd!", "confirm": "different"},
         "two passwords are different"),
    ])
    def test_setup_validates_on_the_server(self, client, app, data, message):
        from app.models import User

        resp = client.post("/setup", data=data)
        assert resp.status_code == 400
        assert message in resp.get_data(as_text=True)
        assert User.query.count() == 0

    def test_change_password_checks_current_and_strength(self, client, floor):
        from app.models import User

        login(client, "operator")
        resp = client.post("/change_password", data={
            "current_password": "wrong", "new_password": "N3w-Synthetic-pw!",
            "confirm_password": "N3w-Synthetic-pw!"})
        assert "That isn&#39;t your current password." in resp.get_data(as_text=True)
        resp = client.post("/change_password", data={
            "current_password": PASSWORD, "new_password": "N3w-Synthetic-pw!",
            "confirm_password": "N3w-Synthetic-pw!"})
        assert resp.headers["Location"] == "/profile"
        assert fresh(User, floor["users"]["operator"]).check_password("N3w-Synthetic-pw!")

    def test_profile_picture_upload_is_stored_as_a_jpeg(self, client, floor, sandbox):
        from app.models import User

        login(client, "operator")
        resp = client.post("/profile", data={
            "profile_picture": (io.BytesIO(png_bytes()), "me.png")},
            content_type="multipart/form-data")
        assert resp.headers["Location"] == "/profile"
        name = fresh(User, floor["users"]["operator"]).profile_picture
        assert name and name.startswith("profile_") and name.endswith(".jpg")  # F-8, 4.7b
        with open(sandbox / "static" / "profile_pics" / name, "rb") as fh:
            assert fh.read(3) == b"\xff\xd8\xff"


# ---------------------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------------------

class TestGames:
    def test_add_game_mints_a_slug_and_a_baseline(self, client, floor, sandbox):
        from app.models import Game, PlayRecord

        login(client, "operator")
        resp = client.post("/add_game", data={
            "name": "Neon Raider", "manufacturer": "Clone Co", "year": "1990",
            "location": "Floor", "status": "Working", "coins_per_play": "0.75",
            "counter_status": "Working", "initial_coin_count": "500"})
        game = Game.query.filter_by(manufacturer="Clone Co").one()
        # Since 4.8a it lands on the new machine's page (where its label is printed).
        assert resp.headers["Location"] == f"/game/{game.id}"
        assert game.barcode == "neon-raider-2"  # the first Neon Raider owns the plain slug
        base = PlayRecord.query.filter_by(game_id=game.id).one()
        assert (base.coin_count, base.plays_count, base.revenue) == (500, 0, 0.0)

    def test_edit_game_updates_every_field(self, client, floor, sandbox):
        from app.models import Game

        login(client, "operator")
        resp = client.post(f"/edit_game/{floor['pinball']}", data={
            "name": "Pixel Pinball DX", "location": "Floor", "status": "Working",
            "coins_per_play": "1.5", "counter_status": "Working", "floor_position": "A3"})
        assert resp.headers["Location"] == f"/game/{floor['pinball']}"
        g = fresh(Game, floor["pinball"])
        assert (g.name, g.location, g.coins_per_play, g.floor_position) == (
            "Pixel Pinball DX", "Floor", 1.5, "A3")
        assert g.barcode == "pixel-pinball"  # renaming never touches the QR slug

    def test_record_plays_turns_a_meter_reading_into_revenue(self, client, floor):
        from app.models import Game

        login(client, "operator")
        resp = client.post(f"/record_plays/{floor['raider']}",
                           data={"coin_count": "150", "date": "2026-10-01"})
        assert resp.headers["Location"] == f"/game/{floor['raider']}"
        g = fresh(Game, floor["raider"])
        assert (g.total_plays, g.total_revenue) == (50, 25.0)  # 10 plays x 0.50 on top

    def test_record_plays_refuses_a_reading_below_the_last(self, client, floor):
        from app.models import PlayRecord

        login(client, "operator")
        resp = client.post(f"/record_plays/{floor['raider']}",
                           data={"coin_count": "120", "date": "2026-10-01"})
        assert resp.status_code == 400  # shown again with the error on the field, 4.8a
        assert "can&#39;t go backwards" in resp.get_data(as_text=True)
        assert PlayRecord.query.filter_by(game_id=floor["raider"]).count() == 2

    def test_record_plays_is_refused_when_the_counter_is_broken(self, client, floor):
        login(client, "operator")
        resp = client.get(f"/record_plays/{floor['courier']}")
        assert resp.headers["Location"] == f"/game/{floor['courier']}"

    def test_delete_play_record_backs_the_totals_out(self, client, floor):
        from app.models import Game, PlayRecord

        rec = PlayRecord.query.filter_by(game_id=floor["raider"], plays_count=40).one()
        login(client, "manager")
        client.post(f"/delete_play_record/{rec.id}")
        g = fresh(Game, floor["raider"])
        assert (g.total_plays, g.total_revenue) == (0, 0.0)

    def test_add_baseline_only_when_there_are_no_records(self, client, floor):
        from app.models import PlayRecord

        login(client, "operator")
        client.post(f"/add_baseline/{floor['courier']}", data={"baseline_coin_count": "77"})
        assert PlayRecord.query.filter_by(game_id=floor["courier"]).one().coin_count == 77
        client.post(f"/add_baseline/{floor['raider']}", data={"baseline_coin_count": "1"})
        assert PlayRecord.query.filter_by(game_id=floor["raider"]).count() == 2

    def test_delete_game_removes_it(self, client, floor, sandbox):
        from app.models import Game

        login(client, "admin")
        resp = client.post(f"/delete_game/{floor['pinball']}")
        assert resp.headers["Location"] == "/games"
        assert fresh(Game, floor["pinball"]) is None

    @pytest.mark.parametrize(("action", "field", "value"), [
        ("move_to_floor", "location", "Floor"),
        ("move_to_warehouse", "location", "Warehouse"),
        ("set_working", "status", "Working"),
        ("set_not_working", "status", "Not_Working"),
    ])
    def test_bulk_update(self, client, floor, action, field, value):
        from app.models import Game

        login(client, "manager")
        client.post("/bulk_update_games", data={
            "action": action, "game_ids": [floor["pinball"], floor["courier"]]})
        for gid in (floor["pinball"], floor["courier"]):
            assert getattr(fresh(Game, gid), field) == value

    def test_import_games_from_a_roster_is_idempotent(self, client, floor):
        from app.models import Game

        roster = {"meta": {"platforms": {}},
                  "video_games": [{"name": "Laser Lagoon", "slug": "laser-lagoon"},
                                  {"name": "Neon Raider", "slug": "neon-raider"}],
                  "pinball": [{"name": "Comet Flipper", "slug": "comet-flipper"}]}
        login(client, "manager")
        for _ in range(2):
            resp = client.post("/import_games", data={
                "file": (io.BytesIO(json.dumps(roster).encode()), "roster.json")},
                content_type="multipart/form-data")
            assert resp.headers["Location"] == "/games"
        assert Game.query.filter_by(barcode="laser-lagoon").count() == 1
        assert Game.query.filter_by(barcode="comet-flipper").count() == 1

    def test_import_games_rejects_non_roster_json(self, client, floor):
        login(client, "manager")
        resp = client.post("/import_games", data={
            "file": (io.BytesIO(b"[1, 2]"), "x.json")}, content_type="multipart/form-data")
        assert resp.headers["Location"] == "/import_games"

    def test_rotating_a_report_token_marks_the_label_stale(self, client, floor):
        from app.extensions import db
        from app.models import Game

        g = fresh(Game, floor["raider"])
        g.mint_report_token()
        db.session.commit()
        old = g.report_token
        login(client, "manager")
        client.post(f"/game/{floor['raider']}/report-token/rotate")
        g = fresh(Game, floor["raider"])
        assert g.report_token != old and g.report_label_stale is True


# ---------------------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------------------

def _order_form(**extra):
    data = {"issue_description": "Screen rolls vertically", "status": "Open",
            "technician": "Tech C", "cost": "", "machine_down": "no",
            "inventory_items-0-item_id": "-1", "inventory_items-0-quantity_used": ""}
    data.update(extra)
    return data


class TestMaintenance:
    def test_a_machine_work_order_uses_parts_from_stock(self, client, floor):
        from app.models import InventoryItem, MaintenanceRecord, StockHistory

        login(client, "operator")
        resp = client.post(f"/maintenance/game/{floor['raider']}", data=_order_form(**{
            "priority": "High", "cost": "5",
            "inventory_items-0-item_id": str(floor["belt"]),
            "inventory_items-0-quantity_used": "2"}))
        assert resp.headers["Location"] == f"/game/{floor['raider']}"
        order = MaintenanceRecord.query.filter_by(
            issue_description="Screen rolls vertically").one()
        assert (order.game_id, order.priority, order.cost) == (floor["raider"], "High", 13.0)
        assert fresh(InventoryItem, floor["belt"]).stock_quantity == 8
        assert StockHistory.query.filter_by(item_id=floor["belt"], change_type="used").count() == 1

    def test_a_general_work_order_has_no_machine(self, client, floor):
        from app.models import MaintenanceRecord

        login(client, "operator")
        resp = client.post("/maintenance/general", data=_order_form(
            work_order_type="plumbing", location_description="Restroom"))
        assert resp.headers["Location"] == "/maintenance_orders"
        order = MaintenanceRecord.query.filter_by(work_order_type="plumbing").one()
        assert order.game_id is None and order.location_description == "Restroom"

    def test_update_logs_work_uses_and_requests_parts(self, client, floor):
        from app.models import (InventoryItem, InventoryRequest, MaintenanceRecord, WorkLog)

        login(client, "operator")
        resp = client.post(f"/update_maintenance/{floor['open_order']}", data={
            "status": "Fixed", "priority": "Low", "fix_description": "New stick",
            "technician": "Tech A", "work_notes": "Swapped the stick", "time_spent": "1.5",
            "inventory_item_0": str(floor["belt"]), "inventory_quantity_0": "1",
            "item_action_0": "use",
            "inventory_item_1": str(floor["fuse"]), "inventory_quantity_1": "4",
            "item_action_1": "request", "urgency_1": "Urgent"})
        assert resp.headers["Location"] == f"/maintenance_detail/{floor['open_order']}"
        order = fresh(MaintenanceRecord, floor["open_order"])
        assert order.status == "Fixed" and order.date_fixed is not None
        assert order.cost == 4.0
        assert WorkLog.query.filter_by(maintenance_id=order.id).count() == 2
        assert fresh(InventoryItem, floor["belt"]).stock_quantity == 9
        req = InventoryRequest.query.filter_by(maintenance_id=order.id).one()
        assert (req.item_id, req.quantity_requested, req.urgency) == (floor["fuse"], 4, "Urgent")

    def test_quick_close(self, client, floor):
        from app.models import MaintenanceRecord

        login(client, "operator")
        client.post(f"/close_maintenance/{floor['open_order']}",
                    data={"status": "Deferred", "fix_description": "Waiting on part"})
        order = fresh(MaintenanceRecord, floor["open_order"])
        assert order.status == "Deferred" and order.date_fixed is not None

    def test_delete_work_order(self, client, floor):
        from app.models import MaintenanceRecord

        login(client, "manager")
        client.post(f"/delete_maintenance/{floor['closed_order']}")
        assert fresh(MaintenanceRecord, floor["closed_order"]) is None

    def test_photo_upload_and_delete(self, client, floor, sandbox):
        from app.models import MaintenanceRecord

        login(client, "manager")
        resp = client.post(f"/maintenance_photos/{floor['open_order']}", data={
            "csrf_token": "present", "photos": [(io.BytesIO(png_bytes()), "drift.png")]},
            content_type="multipart/form-data")
        assert resp.headers["Location"] == f"/maintenance_detail/{floor['open_order']}"
        photos = fresh(MaintenanceRecord, floor["open_order"]).get_photos()
        assert len(photos) == 1
        on_disk = sandbox / "uploads" / "maintenance_photos" / photos[0]
        assert on_disk.exists()

        client.post(f"/delete_maintenance_photo/{floor['open_order']}/{photos[0]}")
        assert fresh(MaintenanceRecord, floor["open_order"]).get_photos() == []
        assert not on_disk.exists()

    def test_photo_upload_refuses_other_extensions(self, client, floor, sandbox):
        login(client, "operator")
        client.post(f"/maintenance_photos/{floor['open_order']}", data={
            "csrf_token": "present", "photos": [(io.BytesIO(b"MZ"), "tool.exe")]},
            content_type="multipart/form-data")
        assert any("not a photo" in m for _, m in flashes(client))


# ---------------------------------------------------------------------------------------
# Inventory, requests and shipments
# ---------------------------------------------------------------------------------------

class TestInventory:
    def test_add_item_records_initial_stock(self, client, floor):
        from app.models import InventoryItem, StockHistory

        login(client, "manager")
        resp = client.post("/inventory/add", data={
            "name": "Trackball 2in", "stock_quantity": "3",
            "minimum_stock": "1", "unit_price": "25", "compatible_games": [floor["raider"]]})
        assert resp.headers["Location"] == "/inventory/"
        item = InventoryItem.query.filter_by(name="Trackball 2in").one()
        assert [g.id for g in item.compatible_games] == [floor["raider"]]
        assert StockHistory.query.filter_by(item_id=item.id).one().reason == "Initial stock"

    def test_edit_item_logs_a_stock_change(self, client, floor):
        from app.models import InventoryItem, StockHistory

        login(client, "manager")
        client.post(f"/inventory/{floor['belt']}/edit", data={
            "name": "Drive Belt", "stock_quantity": "4",
            "minimum_stock": "2", "unit_price": "4"})
        assert fresh(InventoryItem, floor["belt"]).stock_quantity == 4
        assert StockHistory.query.filter_by(item_id=floor["belt"],
                                            change_type="adjusted").one().quantity_change == -6

    @pytest.mark.parametrize(("kind", "qty", "after"), [
        ("added", 5, 15), ("removed", 3, 7), ("used", 20, 0), ("adjusted", 42, 42)])
    def test_adjust_stock(self, client, floor, kind, qty, after):
        from app.models import InventoryItem

        login(client, "operator")
        client.post(f"/inventory/{floor['belt']}/adjust_stock",
                    data={"adjustment_type": kind, "quantity": str(qty), "reason": "count"})
        assert fresh(InventoryItem, floor["belt"]).stock_quantity == after

    def test_stock_falling_below_minimum_raises_an_alert(self, client, floor):
        from app.models import LowStockAlert

        login(client, "operator")
        client.post(f"/inventory/{floor['belt']}/adjust_stock",
                    data={"adjustment_type": "removed", "quantity": "9"})
        assert LowStockAlert.query.filter_by(item_id=floor["belt"], resolved=False).count() == 1

    def test_delete_item(self, client, floor):
        from app.models import InventoryItem

        login(client, "admin")
        client.post(f"/inventory/{floor['belt']}/delete")
        assert fresh(InventoryItem, floor["belt"]) is None

    def test_resolve_alert(self, client, floor):
        from app.models import LowStockAlert

        login(client, "manager")
        client.post(f"/inventory/resolve_alert/{floor['alert']}")
        assert fresh(LowStockAlert, floor["alert"]).resolved is True

    def test_request_an_existing_item_and_a_new_one(self, client, floor):
        from app.models import InventoryRequest

        login(client, "operator")
        client.post("/inventory/request", data={
            "item_type": "existing", "item_id": str(floor["fuse"]), "item_name": "Glass Fuse 2A",
            "quantity": "10", "urgency": "Urgent", "maintenance_id": str(floor["open_order"])})
        client.post("/inventory/request", data={
            "item_type": "new", "item_name": "Spinner knob", "quantity": "2"})
        assert InventoryRequest.query.filter_by(item_id=floor["fuse"]).one().maintenance_id == \
            floor["open_order"]
        assert InventoryRequest.query.filter_by(item_name="Spinner knob").one().item_id is None

    def test_a_request_with_no_quantity_is_refused(self, client, floor):
        login(client, "operator")
        resp = client.post("/inventory/request", data={"item_name": "x", "quantity": "0"})
        assert resp.status_code == 400  # shown again with its errors since 4.5

    def test_receiving_a_request_adds_stock(self, client, floor):
        from app.models import InventoryItem, InventoryRequest

        login(client, "manager")
        client.post(f"/inventory/requests/{floor['request']}/update", data={
            "status": "Received", "tracking_number": "1Z-SYNTH", "vendor": "Parts Depot",
            "estimated_arrival": "2026-10-20"})
        req = fresh(InventoryRequest, floor["request"])
        assert (req.status, req.tracking_number, req.vendor) == (
            "Received", "1Z-SYNTH", "Parts Depot")
        assert req.date_fulfilled is not None
        assert fresh(InventoryItem, floor["belt"]).stock_quantity == 13
        assert len(req.history) >= 4

    def test_receiving_a_free_text_request_creates_the_item(self, client, floor):
        from app.extensions import db
        from app.models import InventoryItem, InventoryRequest

        req = InventoryRequest(item_name="Spinner knob", quantity_requested=2,
                               requested_by_id=floor["users"]["operator"])
        db.session.add(req)
        db.session.commit()
        login(client, "manager")
        client.post(f"/inventory/requests/{req.id}/update", data={"status": "Received"})
        item = InventoryItem.query.filter_by(name="Spinner knob").one()
        assert item.stock_quantity == 2 and fresh(InventoryRequest, req.id).item_id == item.id

    def test_an_operator_may_delete_only_their_own_pending_request(self, client, floor):
        from app.extensions import db
        from app.models import InventoryRequest

        other = InventoryRequest(item_name="Coin door lock", quantity_requested=1,
                                 requested_by_id=floor["users"]["manager"])
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        login(client, "operator")
        client.post(f"/inventory/requests/{other_id}/delete")
        assert fresh(InventoryRequest, other_id) is not None
        client.post(f"/inventory/requests/{floor['request']}/delete")
        assert fresh(InventoryRequest, floor["request"]) is None

    def test_tracking_needs_a_number_then_an_api_key(self, app, client, floor):
        from app.extensions import db
        from app.models import InventoryRequest

        app.config["EASYPOST_API_KEY"] = ""             # read at startup (app/config.py)
        login(client, "manager")
        client.post(f"/inventory/requests/{floor['request']}/update_tracking")
        assert ("error", "No tracking number available for this request.") in flashes(client)
        fresh(InventoryRequest, floor["request"]).tracking_number = "1Z-SYNTH"
        db.session.commit()
        client.post(f"/inventory/requests/{floor['request']}/update_tracking")
        assert any("Tracking API not configured" in m for _, m in flashes(client))

    def test_an_operator_cannot_open_someone_elses_request(self, client, floor):
        from app.extensions import db
        from app.models import InventoryRequest

        other = InventoryRequest(item_name="Coin door lock", quantity_requested=1,
                                 requested_by_id=floor["users"]["manager"])
        db.session.add(other)
        db.session.commit()
        login(client, "operator")
        resp = client.get(f"/inventory/requests/{other.id}")
        assert resp.headers["Location"] == "/inventory/requests"


# ---------------------------------------------------------------------------------------
# Admin: users, storage, backups
# ---------------------------------------------------------------------------------------

class TestAdmin:
    def _user(self, name="temp-user", role="readonly"):
        from app.extensions import db
        from app.models import User

        u = User(username=name, role=role, must_change_password=False)
        u.set_password(PASSWORD)
        db.session.add(u)
        db.session.commit()
        return u.id

    def test_toggle_active(self, client, floor):
        from app.models import User

        uid = self._user()
        login(client, "admin")
        client.post("/admin/users", data={"action": "toggle_active", "user_id": uid})
        assert fresh(User, uid).is_active is False

    def test_reset_password_forces_a_change(self, client, floor):
        from app.models import User

        uid = self._user()
        login(client, "admin")
        client.post("/admin/users", data={"action": "reset_password", "user_id": uid})
        assert fresh(User, uid).must_change_password is True

    def test_change_role_accepts_only_known_roles(self, client, floor):
        from app.models import User

        uid = self._user()
        login(client, "admin")
        client.post("/admin/users", data={"action": "change_role", "user_id": uid,
                                          "new_role": "manager"})
        assert fresh(User, uid).role == "manager"
        client.post("/admin/users", data={"action": "change_role", "user_id": uid,
                                          "new_role": "superuser"})
        assert fresh(User, uid).role == "manager"

    def test_delete_user_but_never_yourself(self, client, floor):
        from app.models import User

        uid = self._user()
        login(client, "admin")
        client.post("/admin/users", data={"action": "delete_user", "user_id": uid})
        assert fresh(User, uid) is None
        client.post("/admin/users", data={"action": "delete_user",
                                          "user_id": floor["users"]["admin"]})
        assert fresh(User, floor["users"]["admin"]) is not None

    @pytest.mark.parametrize(("data", "created"), [
        ({"username": "new-tech", "password": "Long-enough-1", "role": "operator"}, True),
        # Blank: a one-time password is made and shown once (4.7b).
        ({"username": "new-tech", "password": "", "role": "operator"}, True),
        ({"username": "ab", "password": "Long-enough-1", "role": "operator"}, False),
        ({"username": "new-tech", "password": "Sh0rt", "role": "operator"}, False),
        # The same strength rule as a password change since 4.7b (6 characters was enough).
        ({"username": "new-tech", "password": "longenough", "role": "operator"}, False),
        ({"username": "TEST-OPERATOR", "password": "Long-enough-1", "role": "operator"}, False),
        ({"username": "new-tech", "password": "Long-enough-1", "role": "root"}, False),
    ])
    def test_create_user_validation(self, client, floor, data, created):
        from app.models import User

        login(client, "admin")
        client.post("/admin/create_user", data=data)
        made = User.query.filter_by(username="new-tech").first()
        assert (made is not None) == created
        if made:
            assert made.role == "operator" and made.must_change_password is True

    def test_cleanup_removes_photos_older_than_a_year(self, client, floor, sandbox):
        photos = sandbox / "uploads" / "maintenance_photos"
        old, new = photos / "old.jpg", photos / "new.jpg"
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        two_years = time.time() - 2 * 365 * 86400
        os.utime(old, (two_years, two_years))
        login(client, "admin")
        client.post("/admin/cleanup_photos")
        assert not old.exists() and new.exists()

    def _synthetic_db(self, sandbox):
        # backup_database.py verifies these four tables exist in what it copied.
        con = sqlite3.connect(sandbox / "instance" / "arcade.db")
        for table in ("user", "game", "play_record", "maintenance_record"):
            con.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, name TEXT)')
        con.execute("INSERT INTO game (name) VALUES ('Synthetic Machine')")
        con.commit()
        con.close()

    def test_backup_create_list_delete(self, client, floor, sandbox):
        self._synthetic_db(sandbox)
        login(client, "admin")
        client.post("/create_backup")
        made = sorted((sandbox / "backups").glob("arcade_backup_*.db"))
        assert len(made) == 1
        name = made[0].name
        assert name in client.get("/backup_management").get_data(as_text=True)
        assert client.get(f"/download_backup/{name}").status_code == 200  # F-34, 4.7c
        client.post("/delete_backup", data={"backup_file": name})
        assert not made[0].exists()

    def test_download_refuses_a_name_without_the_backup_prefix(self, client, floor, sandbox):
        (sandbox / "backups" / "other.db").write_bytes(b"x")
        login(client, "admin")
        resp = client.get("/download_backup/other.db")
        assert resp.headers["Location"] == "/backup_management"

    def test_restore_runs_the_restore_script(self, client, floor, sandbox):
        self._synthetic_db(sandbox)
        login(client, "admin")
        client.post("/create_backup")
        name = next((sandbox / "backups").glob("arcade_backup_*.db")).name
        client.post("/restore_backup", data={"backup_file": name})
        assert any("restored" in m.lower() or "restore" in m.lower() for _, m in flashes(client))
