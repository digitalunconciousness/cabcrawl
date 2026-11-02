"""scripts/reset_history.py: delete the history, keep the machines."""
from __future__ import annotations

import gzip
import importlib.util
import os
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reset_history.py"


@pytest.fixture()
def reset():
    spec = importlib.util.spec_from_file_location("reset_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def dump(tmp_path):
    path = tmp_path / "pre-reset.sql.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("-- PostgreSQL database dump\nCREATE TABLE public.maintenance_record ();\n")
        fh.write(os.urandom(4096).hex())  # incompressible, like real row data
    return str(path)


def _snapshot():
    from app.models import (
        Device, Game, InventoryItem, InventoryRequest, MaintenanceRecord, PlayRecord,
        RailSession, StockHistory, User, WorkLog,
    )
    return {m.__name__: m.query.count() for m in (
        Device, Game, InventoryItem, InventoryRequest, MaintenanceRecord, PlayRecord,
        RailSession, StockHistory, User, WorkLog)}


def test_without_yes_it_only_counts(app, floor, sandbox, reset, capsys):
    before = _snapshot()
    assert reset.main([], app=app) == 0
    assert _snapshot() == before
    out = capsys.readouterr().out
    assert "Would delete:" in out and "Nothing was changed" in out


@pytest.mark.parametrize("why", ["missing", "absent", "tiny", "old", "not-a-dump"])
def test_it_refuses_without_a_fresh_real_backup(app, floor, sandbox, reset, tmp_path, why):
    path = {"missing": None, "absent": str(tmp_path / "nope.sql.gz")}.get(why)
    if why == "tiny":
        path = str(tmp_path / "tiny.sql")
        Path(path).write_text("x")
    elif why == "old":
        path = str(tmp_path / "old.sql")
        Path(path).write_text("-- PostgreSQL database dump\n" + "x" * 4096)
        stale = time.time() - 5 * 3600
        os.utime(path, (stale, stale))
    elif why == "not-a-dump":
        path = str(tmp_path / "notes.txt")
        Path(path).write_text("shopping list\n" * 400)
    before = _snapshot()
    args = ["--yes"] + (["--backup", path] if path else [])
    assert reset.main(args, app=app) == 2
    assert _snapshot() == before


def test_it_deletes_the_history_and_keeps_the_machines(app, floor, sandbox, reset, dump):
    from app.models import (
        Game, InventoryItem, InventoryRequest, LowStockAlert, MaintenanceRecord, PlayRecord,
        RailSession, StockHistory, User, WorkLog, item_game_compatibility,
    )
    from app.extensions import db

    raider = db.session.get(Game, floor["raider"])
    raider.mint_report_token()
    raider.times_in_top_5 = 3
    db.session.commit()
    keep = {g.id: (g.name, g.barcode, g.report_token, g.location, g.status)
            for g in Game.query.all()}
    users, sessions = User.query.count(), RailSession.query.count()
    (sandbox / "uploads" / "maintenance_photos" / "maintenance_2_cd.jpg").write_bytes(b"x")
    (sandbox / "static" / "maintenance_photos" / "maintenance_1_ab.jpg").write_bytes(b"x")

    assert reset.main(["--yes", "--backup", dump], app=app) == 0
    db.session.expire_all()  # the script ran in its own app context, so its own session

    for model in (MaintenanceRecord, WorkLog, PlayRecord, InventoryItem, InventoryRequest,
                  StockHistory, LowStockAlert):
        assert model.query.count() == 0, model.__name__
    assert db.session.execute(db.select(db.func.count()).select_from(
        item_game_compatibility)).scalar_one() == 0
    assert {g.id: (g.name, g.barcode, g.report_token, g.location, g.status)
            for g in Game.query.all()} == keep
    for g in Game.query.all():
        assert (g.total_plays, g.total_revenue, g.times_in_top_5, g.times_in_top_10,
                g.last_ranking_update) == (0, 0.0, 0, 0, None)
    assert User.query.count() == users and RailSession.query.count() == sessions
    assert list((sandbox / "static" / "maintenance_photos").iterdir()) == []
    assert list((sandbox / "uploads" / "maintenance_photos").iterdir()) == []


def test_it_clears_the_inventory_and_keeps_the_places(app, floor, sandbox, reset, dump):
    from app.extensions import db
    from app.models import (
        GameEvent, ItemSupplier, MaintenanceRecord, MaintenanceTask, PartCategory,
        PartReservation, PriceChange, PurchaseOrder, PurchaseOrderLine, SerialUnit, StockCount,
        StockCountLine, StockLevel, StockPlace, Supplier, TaskPart, UnitEvent,
    )

    order = MaintenanceRecord.query.first()
    db.session.add(GameEvent(game_id=order.game_id, kind="status", new_value="Down",
                             maintenance_id=order.id))
    db.session.commit()
    kept = (StockPlace, PartCategory, Supplier, MaintenanceTask)
    before = {m.__name__: m.query.count() for m in kept}
    cleared = (ItemSupplier, PriceChange, PurchaseOrder, PurchaseOrderLine, SerialUnit,
               UnitEvent, StockCount, StockCountLine, StockLevel, PartReservation, TaskPart)
    assert all(m.query.count() for m in (StockLevel, PurchaseOrderLine, SerialUnit,
                                          StockCountLine, PartReservation, TaskPart))

    assert reset.main(["--yes", "--backup", dump], app=app) == 0
    db.session.expire_all()
    for model in cleared:
        assert model.query.count() == 0, model.__name__
    assert {m.__name__: m.query.count() for m in kept} == before
    event = GameEvent.query.filter_by(new_value="Down").one()
    assert event.maintenance_id is None


def test_nothing_is_left_pointing_at_a_deleted_row(reset):
    """A table added later that points at what the reset deletes must be added to it too."""
    from app.extensions import db

    deleted = {getattr(t, "__table__", t).name for _label, t in reset.DELETE_ORDER}
    # Kept on purpose: the machine timeline (its work-order link is cleared first).
    kept = {"game_event"}
    for table in db.metadata.sorted_tables:
        if table.name in deleted or table.name in kept:
            continue
        hits = {fk.column.table.name for fk in table.foreign_keys} & deleted
        assert not hits, f"{table.name} points at {hits}, which the reset deletes"
    # Children before parents: each table is deleted before any table it points at.
    order = [getattr(t, "__table__", t).name for _label, t in reset.DELETE_ORDER]
    for i, name in enumerate(order):
        parents = {fk.column.table.name for fk in db.metadata.tables[name].foreign_keys}
        assert not parents & set(order[:i]), f"{name} is deleted after its parent"


def test_the_tracker_works_afterwards(app, client, floor, sandbox, reset, dump):
    from char_support import login

    reset.main(["--yes", "--backup", dump], app=app)
    login(client, "admin")
    for url in ("/", "/games", f"/game/{floor['raider']}", "/maintenance_orders",
                "/inventory/", "/reports"):
        assert client.get(url).status_code == 200, url
