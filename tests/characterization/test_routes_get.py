"""Every GET page, for every role: status, where a refusal goes, and one fact on the page.

Characterization, not specification: this pins what the application does today so the
redesign can prove it changed the look and nothing else. Where today's behaviour is a known
bug, the bug is pinned here as-is and its fix is a test in ``test_flags.py`` (strict xfail),
so fixing it flips that test instead of quietly passing.

The table is checked against the route map: a new GET route that is not listed here fails
``test_every_get_route_is_in_the_matrix``.
"""
from __future__ import annotations

import pytest

from char_support import LEVEL, login

# (rule, url builder, minimum role, expected status when allowed, text on the page or None,
#  content type prefix)
#
# The minimum role is what the decorators enforce today, read from FEATURES.md and the code.
PAGES = [
    ("/", lambda f: "/", "readonly", 200, "Neon Raider", "text/html"),
    ("/games", lambda f: "/games", "readonly", 200, "Pixel Pinball", "text/html"),
    ("/add_game", lambda f: "/add_game", "operator", 200, 'name="name"', "text/html"),
    # The work-order history on this page is manager-only (test_visibility pins that).
    ("/game/<int:game_id>", lambda f: f"/game/{f['raider']}", "readonly", 200,
     "Synthwave Co", "text/html"),
    ("/edit_game/<int:game_id>", lambda f: f"/edit_game/{f['raider']}", "operator", 200,
     "Synthwave Co", "text/html"),
    ("/record_plays/<int:game_id>", lambda f: f"/record_plays/{f['raider']}", "operator", 200,
     "140", "text/html"),
    # The collection round (feature step 3).
    ("/round/", lambda f: "/round/", "operator", 200, "Neon Raider", "text/html"),
    ("/round/<int:game_id>", lambda f: f"/round/{f['raider']}", "operator", 200, "140",
     "text/html"),
    ("/rotation/", lambda f: "/rotation/", "manager", 200, "Neon Raider", "text/html"),
    ("/maintenance/tasks/", lambda f: "/maintenance/tasks/", "readonly", 200,
     "Lube the trackball", "text/html"),
    ("/maintenance/tasks/new", lambda f: "/maintenance/tasks/new", "manager", 200,
     'name="every_plays"', "text/html"),
    ("/maintenance/tasks/<int:task_id>/edit", lambda f: f"/maintenance/tasks/{f['task']}/edit",
     "manager", 200, "Lube the trackball", "text/html"),
    ("/admin/alerts/", lambda f: "/admin/alerts/", "admin", 200, "Techs", "text/html"),
    ("/admin/alerts/new", lambda f: "/admin/alerts/new", "admin", 200, 'name="discord_webhook"',
     "text/html"),
    ("/admin/alerts/<int:channel_id>/edit", lambda f: f"/admin/alerts/{f['channel']}/edit",
     "admin", 200, "arcade-techs", "text/html"),
    ("/export_selected_games", lambda f: f"/export_selected_games?game_ids={f['raider']}",
     "manager", 200, "Neon Raider", "text/csv"),
    ("/import_games", lambda f: "/import_games", "manager", 200, 'type="file"', "text/html"),
    ("/scan", lambda f: "/scan", "readonly", 200, "/g/", "text/html"),
    # Places (inventory step 1).
    ("/inventory/places/", lambda f: "/inventory/places/", "operator", 200, "Shelf A",
     "text/html"),
    ("/inventory/places/<int:place_id>", lambda f: f"/inventory/places/{f['shelf']}",
     "operator", 200, "Drive Belt", "text/html"),
    # Part details (inventory step 2).
    ("/inventory/categories", lambda f: "/inventory/categories", "manager", 200,
     "Belts &amp; mechanical", "text/html"),
    ("/inventory/suppliers", lambda f: "/inventory/suppliers", "operator", 200, "Parts Depot",
     "text/html"),
    ("/inventory/suppliers/<int:supplier_id>",
     lambda f: f"/inventory/suppliers/{f['supplier']}", "operator", 200, "PD-BELT",
     "text/html"),
    # Purchasing (inventory step 3).
    ("/inventory/shopping", lambda f: "/inventory/shopping", "manager", 200, "Glass Fuse 2A",
     "text/html"),
    ("/inventory/orders", lambda f: "/inventory/orders", "operator", 200, "Parts Depot",
     "text/html"),
    ("/inventory/orders/<int:order_id>", lambda f: f"/inventory/orders/{f['order']}",
     "operator", 200, "Synthetic order", "text/html"),
    # Planned counts (inventory step 5).
    ("/inventory/counts/", lambda f: "/inventory/counts/", "operator", 200, "Shelf A count",
     "text/html"),
    ("/inventory/counts/<int:count_id>", lambda f: f"/inventory/counts/{f['stock_count']}",
     "operator", 200, "Drive Belt", "text/html"),
    ("/inventory/counts/<int:count_id>/review",
     lambda f: f"/inventory/counts/{f['stock_count']}/review", "manager", 200,
     "Shelf A count", "text/html"),
    # Serialized spares (inventory step 4).
    ("/inventory/units/<int:unit_id>", lambda f: f"/inventory/units/{f['unit']}", "operator",
     200, "CPU-0002", "text/html"),
    ("/inventory/labels", lambda f: "/inventory/labels", "operator", 200, "Shelf A",
     "text/html"),
    ("/inventory/labels/sheet",
     lambda f: f"/inventory/labels/sheet?ids=p{f['shelf']},i{f['belt']}", "operator", 200,
     "/p/", "text/html"),
    ("/inventory/places/<int:place_id>/count",
     lambda f: f"/inventory/places/{f['shelf']}/count", "operator", 200, "Drive Belt",
     "text/html"),
    ("/game/<int:game_id>/label", lambda f: f"/game/{f['raider']}/label", "operator", 200,
     "/g/neon-raider", "text/html"),
    ("/labels", lambda f: "/labels", "operator", 200, "Star Courier", "text/html"),
    ("/labels/sheet", lambda f: f"/labels/sheet?ids={f['raider']},{f['pinball']}", "operator",
     200, "Pixel Pinball", "text/html"),
    ("/labels/coindoor", lambda f: "/labels/coindoor", "manager", 200, "Star Courier",
     "text/html"),
    # GET only shows the sheet; a machine with no code yet is offered the print button.
    ("/labels/coindoor/sheet", lambda f: f"/labels/coindoor/sheet?ids={f['raider']}",
     "manager", 200, "Neon Raider", "text/html"),
    # maintenance
    ("/maintenance/game/<int:game_id>", lambda f: f"/maintenance/game/{f['raider']}",
     "operator", 200, "Neon Raider", "text/html"),
    ("/maintenance/general", lambda f: "/maintenance/general", "operator", 200,
     'name="issue_description"', "text/html"),
    ("/maintenance_orders", lambda f: "/maintenance_orders", "readonly", 200,
     "Ceiling light flickers", "text/html"),
    ("/maintenance_detail/<int:record_id>", lambda f: f"/maintenance_detail/{f['open_order']}",
     "readonly", 200, "Reseated the harness", "text/html"),
    ("/update_maintenance/<int:record_id>", lambda f: f"/update_maintenance/{f['open_order']}",
     "operator", 200, "Drive Belt", "text/html"),
    ("/download_maintenance_record/<int:record_id>",
     lambda f: f"/download_maintenance_record/{f['open_order']}", "readonly", 200, None,
     "application/pdf"),
    ("/maintenance_photos/<int:maintenance_id>",
     lambda f: f"/maintenance_photos/{f['open_order']}", "operator", 200, 'type="file"',
     "text/html"),
    ("/maintenance_reports", lambda f: "/maintenance_reports", "manager", 200,
     "Coin mech jammed", "text/html"),
    ("/export_maintenance_report", lambda f: "/export_maintenance_report?type=all&days=30",
     "manager", 200, None, "application/pdf"),
    # inventory
    ("/inventory/", lambda f: "/inventory/", "operator", 200, "Glass Fuse 2A", "text/html"),
    ("/inventory/add", lambda f: "/inventory/add", "manager", 200, 'name="name"', "text/html"),
    ("/inventory/<int:item_id>", lambda f: f"/inventory/{f['belt']}", "operator", 200,
     "BELT-01", "text/html"),
    ("/inventory/<int:item_id>/edit", lambda f: f"/inventory/{f['belt']}/edit", "manager", 200,
     "Parts Depot", "text/html"),
    ("/inventory/<int:item_id>/adjust_stock", lambda f: f"/inventory/{f['belt']}/adjust_stock",
     "operator", 200, "Drive Belt", "text/html"),
    ("/inventory/low_stock_alerts", lambda f: "/inventory/low_stock_alerts", "manager", 200,
     "Glass Fuse 2A", "text/html"),
    ("/inventory/request", lambda f: "/inventory/request", "operator", 200, "Drive Belt",
     "text/html"),
    ("/inventory/requests", lambda f: "/inventory/requests", "operator", 200, "Drive Belt",
     "text/html"),
    ("/inventory/requests/<int:request_id>", lambda f: f"/inventory/requests/{f['request']}",
     "operator", 200, "Spare stock", "text/html"),
    # reports
    ("/reports", lambda f: "/reports", "manager", 200, "Neon Raider", "text/html"),
    ("/revenue_reports", lambda f: "/revenue_reports?days=30", "manager", 200, "Neon Raider",
     "text/html"),
    ("/graphs", lambda f: "/graphs", "manager", 200, "chart", "text/html"),
    ("/export_report", lambda f: "/export_report", "manager", 200, None, "application/pdf"),
    ("/export_report_debug", lambda f: "/export_report_debug", "manager", 200, None,
     "application/pdf"),
    ("/export_revenue_report", lambda f: "/export_revenue_report?days=30", "manager", 200,
     None, "application/pdf"),
    ("/export_csv", lambda f: "/export_csv", "manager", 200, "Star Courier", "text/csv"),
    # admin
    ("/admin/users", lambda f: "/admin/users", "admin", 200, "test-operator", "text/html"),
    ("/admin/create_user", lambda f: "/admin/create_user", "admin", 200, 'name="username"',
     "text/html"),
    ("/admin/storage", lambda f: "/admin/storage", "admin", 200, "500", "text/html"),
    ("/backup_management", lambda f: "/backup_management", "admin", 200, None, "text/html"),
    ("/styleguide", lambda f: "/styleguide", "admin", 200, "Every component", "text/html"),
    ("/admin/branding", lambda f: "/admin/branding", "admin", 200, 'name="arcade_name"',
     "text/html"),
    ("/admin/devices/", lambda f: "/admin/devices/", "admin", 200, "bench-test", "text/html"),
    ("/admin/devices/<int:device_id>", lambda f: f"/admin/devices/{f['device']}", "admin", 200,
     "neon-raider", "text/html"),
    # GATBOX rail history (Phase 1 data; restyle only)
    ("/rails/", lambda f: "/rails/", "readonly", 200, "Neon Raider", "text/html"),
    ("/rails/machine/<slug>", lambda f: "/rails/machine/neon-raider", "readonly", 200,
     "Neon Raider", "text/html"),
    ("/rails/session/<uid>", lambda f: f"/rails/session/{f['session_uid']}", "readonly", 200,
     "+5V", "text/html"),
    # account
    ("/profile", lambda f: "/profile", "readonly", 200, "test-", "text/html"),
    ("/change_password", lambda f: "/change_password", "readonly", 200,
     'name="current_password"', "text/html"),
]

# GET routes deliberately outside this matrix, with where they are covered instead.
ELSEWHERE = {
    "/g/<code>": "test_qr_path.py: a redirect, pinned on its own",
    "/p/<code>": "tests/test_stock_labels.py: a place's label, a redirect",
    "/i/<code>": "tests/test_stock_labels.py: a part's label, a redirect",
    "/inventory/photos/<filename>": "tests/test_part_details.py: needs an uploaded photo",
    "/u/<code>": "tests/test_serial_units.py: a unit's label, a redirect",
    "/download_backup/<filename>": "test_routes_post.py: needs a backup file on disk",
    "/machine-images/<path:filename>": "test_machine_page.py: needs an uploaded image",
    "/maintenance/photos/<filename>": "tests/test_photo_storage.py: needs a photo on disk",
    "/static/maintenance_photos/<filename>": (
        "tests/test_photo_storage.py: the old photo address, a redirect"),
    "/login": "test_routes_post.py",
    "/logout": "test_routes_post.py",
    "/setup": "test_routes_post.py",
    "/report/<token>": "tests/test_coin_door_report.py (public, token-authorised)",
    "/branding/logo": "tests/test_branding.py: public, needs an uploaded logo",
    "/branding/background": "tests/test_branding.py: public, needs an uploaded background",
    "/branding/print-logo": "tests/test_branding.py: public, needs an uploaded print logo",
    "/api/v1/health": "tests/test_api_v1_contract.py (Phase 1 hub, untouched)",
    "/api/v1/roster": "tests/test_api_v1_contract.py",
    "/api/v1/machines/<slug>/orders": "tests/test_api_v1_contract.py",
    "/static/<path:filename>": "static files",
}
RETIRING = "/skeeball"  # retired 2026-10-08; test_route_authentication asserts it is gone


def test_every_get_route_is_in_the_matrix(app):
    listed = {p[0] for p in PAGES} | set(ELSEWHERE)
    missing = sorted(
        r.rule for r in app.url_map.iter_rules()
        if "GET" in r.methods and r.rule not in listed and not r.rule.startswith(RETIRING)
    )
    assert missing == [], f"GET routes with no characterization: {missing}"


CASES = [(p, role) for p in PAGES for role in LEVEL]


@pytest.mark.parametrize(("page", "role"), CASES,
                         ids=[f"{p[0]}-{role}" for p, role in CASES])
def test_get_page(client, floor, page, role):
    rule, build, minimum, status, text, ctype = page
    login(client, role)
    resp = client.get(build(floor))

    if role == "anon":
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        return
    if LEVEL[role] < LEVEL[minimum]:
        # Refused by requires_role: a flash and a redirect home, not a 403.
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")
        return

    assert resp.status_code == status, resp.get_data(as_text=True)[:500]
    assert resp.content_type.startswith(ctype)
    if ctype == "application/pdf":
        assert resp.data.startswith(b"%PDF")
    if text is not None:
        assert text in resp.get_data(as_text=True)
