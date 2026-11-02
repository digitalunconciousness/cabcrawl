"""The usability round: who reported and fixed an order, the machine page's buttons and photo,
and a home screen per role. Owner's decisions of 2026-10-25."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "characterization"))
from char_support import login
from seed import seed_floor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def floor(app):
    return seed_floor()


def newest(game_id):
    from app.extensions import db
    from app.models import MaintenanceRecord

    db.session.expire_all()
    return (MaintenanceRecord.query.filter_by(game_id=game_id)
            .order_by(MaintenanceRecord.id.desc()).first())


def report(client, game_id, **extra):
    data = {"issue_description": "Screen is black", "status": "Open", "machine_down": "no",
            **extra}
    return client.post(f"/maintenance/game/{game_id}", data=data)


# --- who reported it, who fixed it --------------------------------------------------------------

def test_a_signed_in_report_records_who(client, floor):
    login(client, "operator")
    report(client, floor["courier"])
    order = newest(floor["courier"])
    assert order.reported_by_user.username == "test-operator"
    html = client.get(f"/maintenance_detail/{order.id}").get_data(as_text=True)
    assert "Reported by</dt><dd>test-operator" in html


def test_closing_as_fixed_records_who_and_the_machine_page_says_so(client, floor):
    login(client, "operator")
    report(client, floor["courier"])
    order = newest(floor["courier"])
    client.post(f"/close_maintenance/{order.id}", data={
        "status": "Fixed", "fix_description": "Reseated the video cable", "technician": ""})
    order = newest(floor["courier"])
    assert order.fixed_by_user.username == "test-operator"
    html = client.get(f"/game/{floor['courier']}").get_data(as_text=True)
    assert "Last fixed" in html and "Reseated the video cable" in html
    assert "by test-operator" in html


def test_deferring_isnt_fixing(client, floor):
    login(client, "operator")
    report(client, floor["courier"])
    order = newest(floor["courier"])
    client.post(f"/close_maintenance/{order.id}", data={"status": "Deferred",
                                                        "fix_description": "Later"})
    assert newest(floor["courier"]).fixed_by_id is None


def test_an_older_order_falls_back_to_its_technician(client, floor):
    """The seeded closed order predates the column: its technician is who fixed it."""
    login(client, "readonly")
    html = client.get(f"/game/{floor['raider']}").get_data(as_text=True)
    assert "Last fixed" in html and "Cleared the chute" in html


def test_everyone_sees_how_it_was_fixed_last_time(client, floor):
    login(client, "readonly")
    assert "Last fixed" in client.get(f"/game/{floor['raider']}").get_data(as_text=True)


@pytest.mark.parametrize(("source", "name", "expected"), [
    ("coindoor", "Sam Tester", "Sam Tester, at the coin door"),
    ("coindoor", None, "The coin door"),
    ("gatbox", None, "GATBOX"),
    ("pm", None, "Preventive maintenance"),
    ("web", None, None),
])
def test_how_a_reporter_reads(app, source, name, expected):
    from app.models import MaintenanceRecord

    assert MaintenanceRecord(source=source, reported_by=name).reporter == expected


# --- the machine page: three big buttons and a photo first --------------------------------------

def actions(html):
    start = html.index('class="machine__actions"')
    return html[start:html.index("</nav>", start)]


def test_staff_get_the_three_buttons_and_readonly_doesnt(client, floor):
    login(client, "readonly")
    assert 'class="machine__actions"' not in client.get(f"/game/{floor['raider']}").get_data(
        as_text=True)
    client.post("/logout")
    login(client, "operator")
    nav = actions(client.get(f"/game/{floor['raider']}").get_data(as_text=True))
    assert f'href="/record_plays/{floor["raider"]}">Record plays' in nav
    assert 'href="#report">Report problem' in nav


def test_log_work_goes_straight_to_a_single_open_order(client, floor):
    login(client, "operator")
    nav = actions(client.get(f"/game/{floor['raider']}").get_data(as_text=True))
    assert f'href="/update_maintenance/{floor["open_order"]}">Log work' in nav


def test_log_work_with_several_open_lists_them_and_with_none_offers_a_report(client, floor):
    login(client, "operator")
    report(client, floor["raider"])
    nav = actions(client.get(f"/game/{floor['raider']}").get_data(as_text=True))
    assert 'href="#open-title">Log work<span class="btn__sub">2 open, pick one' in nav
    nav = actions(client.get(f"/game/{floor['courier']}").get_data(as_text=True))
    assert 'href="#report">Log work<span class="btn__sub">Nothing open' in nav


def test_record_plays_says_why_not_when_the_counter_is_broken(client, floor):
    login(client, "operator")
    nav = actions(client.get(f"/game/{floor['courier']}").get_data(as_text=True))
    assert 'aria-disabled="true">Record plays<span class="btn__sub">Counter not working' in nav


def test_the_report_asks_for_a_photo_first_and_keeps_it(client, app, floor, tmp_path):
    import io

    from PIL import Image

    app.config["UPLOAD_FOLDER"] = str(tmp_path)       # keep the photo out of the checkout
    login(client, "operator")
    html = client.get(f"/game/{floor['courier']}").get_data(as_text=True)
    form = html[html.index('id="report"'):]
    assert 'enctype="multipart/form-data"' in form
    assert form.index('name="photos"') < form.index('name="issue_description"')
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 90)).save(buf, "PNG")
    buf.seek(0)
    resp = client.post(f"/maintenance/game/{floor['courier']}",
                       content_type="multipart/form-data",
                       data={"issue_description": "Cracked glass", "status": "Open",
                             "machine_down": "no", "photos": (buf, "glass.png")})
    assert resp.status_code == 302
    order = newest(floor["courier"])
    (name,) = order.get_photos()
    assert name.endswith(".jpg")
    assert os.path.exists(os.path.join(tmp_path, "maintenance_photos", name))


# --- a home screen per role --------------------------------------------------------------------

def home(client, role):
    login(client, role)
    return client.get("/").get_data(as_text=True)


def order_of(html, *titles):
    return [html.index(t) for t in titles]


def test_readonly_sees_whats_down_first(client, floor):
    html = home(client, "readonly")
    assert order_of(html, 'id="down-title"', 'id="floor-title"', 'id="orders-title"') == sorted(
        order_of(html, 'id="down-title"', 'id="floor-title"', 'id="orders-title"'))
    assert "Nothing down" in html and "Plays this week" not in html


def test_an_operator_sees_their_orders_first_and_whats_due(client, floor):
    from app.extensions import db
    from app.models import MaintenanceRecord

    # Theirs by name; the seeded joystick order is theirs too (they logged work on it), and
    # the general order isn't.
    db.session.add(MaintenanceRecord(game_id=floor["courier"], issue_description="Mine to do",
                                     status="Open", technician="test-operator"))
    db.session.add(MaintenanceRecord(game_id=floor["raider"], status="Open", source="pm",
                                     issue_description="Preventive maintenance: Lube.\nmore"))
    db.session.commit()
    html = home(client, "operator")
    orders = html[html.index('id="orders-title"'):html.index("</section>",
                                                            html.index('id="orders-title"'))]
    assert orders.index("Mine to do") < orders.index("Ceiling light flickers")
    assert orders.index("Joystick drifts left") < orders.index("Ceiling light flickers")
    assert orders.count(">Yours<") == 2
    assert 'id="pm-title"' in html and "Preventive maintenance: Lube." in html
    assert html.index('id="orders-title"') < html.index('id="down-title"') if (
        'id="down-title"' in html) else True
    assert "Plays this week" not in html and "Suggested swaps" not in html


def test_work_logged_makes_an_order_yours(app, floor):
    from app.extensions import db
    from app.models import MaintenanceRecord, User, WorkLog
    from app.services.dashboard import _mine

    db.session.add(WorkLog(maintenance_id=floor["open_order"],
                           user_id=floor["users"]["operator"], work_description="Looked"))
    db.session.commit()
    operator = User.query.filter_by(username="test-operator").one()
    orders = MaintenanceRecord.query.filter_by(status="Open").all()
    assert floor["open_order"] in _mine(operator, orders)


def test_a_manager_sees_plays_this_week_downtime_and_swaps(client, floor):
    from app.extensions import db
    from app.models import Game, PlayRecord
    from app.services.collection import local_today

    db.session.add(PlayRecord(game_id=floor["raider"], coin_count=200, plays_count=60,
                              revenue=30.0, date_recorded=local_today()))
    db.session.get(Game, floor["courier"]).status = "Not_Working"
    db.session.commit()
    html = home(client, "manager")
    # With the seeded reading of 5 days ago (40 plays): 100 this week, none the week before.
    assert "Plays this week" in html and "+100 on last week" in html
    assert "$50.00 in tokens" in html
    assert "down since today" in html
    assert 'id="swaps-title"' in html
    assert html.index('id="down-title"') < html.index('id="swaps-title"')


def test_an_admin_is_told_when_alerts_failed(client, floor):
    from app.extensions import db
    from app.models import Alert

    db.session.add(Alert(channel_id=floor["channel"], target="ntfy", event="coin_door",
                         title="x", body="", status="failed"))
    db.session.commit()
    html = home(client, "admin")
    assert "1 alert not sent this week" in html
    client.post("/logout")
    assert "not sent this week" not in home(client, "manager")


def test_the_migration_round_trips(app, floor):
    import sqlalchemy as sa
    from flask_migrate import downgrade, stamp, upgrade

    from app.extensions import db

    def migrate(step, revision):
        db.session.commit()
        step(directory=os.path.join(ROOT, "migrations"), revision=revision)

    def columns():
        return {c["name"] for c in sa.inspect(db.engine).get_columns("maintenance_record")}

    new = {"reported_by", "reported_by_id", "fixed_by_id"}
    stamp(directory=os.path.join(ROOT, "migrations"), revision="b6c7d8e9f0a1")
    migrate(downgrade, "a5b6c7d8e9f0")
    assert not new & columns()
    migrate(upgrade, "b6c7d8e9f0a1")
    migrate(upgrade, "b6c7d8e9f0a1")
    assert new <= columns()
