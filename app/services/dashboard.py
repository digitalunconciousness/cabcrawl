"""The dashboard: what needs attention on the floor today, for whoever is looking.

Each role sees what it acts on first (owner's decision, 2026-10-25, usability round):

* readonly -- what's down, and the floor;
* operator -- open orders (their own first), machines down, maintenance due, low stock;
* manager  -- plays this week against last week, downtime, the rotation planner's swaps,
  low stock;
* admin    -- the manager's view, and alerts that failed to send.

The costlier parts (the rotation plan, downtime from the history) are only worked out for the
roles that see them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import joinedload

from app.models import Game, InventoryItem, LowStockAlert, MaintenanceRecord, PlayRecord
from app.services.work_orders import PRIORITY_RANK as PRIORITY_ORDER

OPEN = ("Open", "In_Progress")
DOWN = ("Not_Working", "Being_Fixed")


@dataclass
class Dashboard:
    total_games: int
    floor_games: list[Game]
    down_games: list[Game]
    total_plays: int
    total_revenue: float
    open_orders: list[MaintenanceRecord]
    open_order_count: int
    recent_records: list[PlayRecord]
    low_stock: list[LowStockAlert]
    # Machines whose coin-door label was retired and not yet replaced: the old label still
    # scans and leads nowhere, so nothing else on the floor would say so.
    stale_labels: list[Game]
    role: str = "readonly"
    mine: set[int] = field(default_factory=set)            # open order ids that are theirs
    pm_orders: list[MaintenanceRecord] = field(default_factory=list)
    down_since: dict[int, dt.date] = field(default_factory=dict)
    plays_week: int = 0
    plays_last_week: int = 0
    value_week: float = 0.0
    swaps: list = field(default_factory=list)                # rotation.Swap
    failed_alerts: int = 0
    inventory: object = None                                 # inventory_due.InventoryDue


def _down_since(games: list[Game]) -> dict[int, dt.date]:
    """When each down machine went down, from the history (the latest change into a down
    status). A machine down since before history started has no date."""
    from app.models import GameEvent

    ids = [g.id for g in games]
    if not ids:
        return {}
    out: dict[int, dt.date] = {}
    events = (GameEvent.query.filter(GameEvent.game_id.in_(ids), GameEvent.kind == "status")
              .order_by(GameEvent.occurred_at, GameEvent.id).all())
    for e in events:
        if e.new_value in DOWN and e.old_value not in DOWN and e.source != "baseline":
            out[e.game_id] = e.occurred_at.date()
        elif e.new_value not in DOWN:
            out.pop(e.game_id, None)
    return out


def _mine(user, orders: list[MaintenanceRecord]) -> set[int]:
    """Orders assigned to *user* by name, or that they have logged work on."""
    from app.models import WorkLog

    name = (user.username or "").strip().lower()
    logged = {w.maintenance_id for w in WorkLog.query.filter(
        WorkLog.user_id == user.id,
        WorkLog.maintenance_id.in_([o.id for o in orders] or [0])).all()}
    return {o.id for o in orders
            if o.id in logged or (o.technician or "").strip().lower() == name}


def _plays_between(first: dt.date, last: dt.date) -> tuple[int, float]:
    rows = PlayRecord.query.filter(PlayRecord.date_recorded >= first,
                                   PlayRecord.date_recorded <= last).all()
    return sum(r.plays_count or 0 for r in rows), sum(r.revenue or 0.0 for r in rows)


def build_dashboard(user=None) -> Dashboard:
    games = Game.query.order_by(Game.name).all()
    floor = [g for g in games if g.location == "Floor"]

    open_orders = (
        MaintenanceRecord.query.options(joinedload(MaintenanceRecord.game))
        .filter(MaintenanceRecord.status.in_(OPEN))
        .all()
    )
    open_orders.sort(key=lambda o: (PRIORITY_ORDER.get(o.priority, 9),
                                    -(o.date_reported.timestamp() if o.date_reported else 0)))

    recent = (
        PlayRecord.query.options(joinedload(PlayRecord.game))
        .order_by(PlayRecord.date_recorded.desc(), PlayRecord.id.desc())
        .limit(5)
        .all()
    )
    low = (
        LowStockAlert.query.options(joinedload(LowStockAlert.item))
        .join(InventoryItem)
        .filter(LowStockAlert.resolved.is_(False))
        .order_by(InventoryItem.name)
        .limit(10)
        .all()
    )

    d = Dashboard(
        total_games=len(games),
        floor_games=floor,
        down_games=[g for g in games if g.status in DOWN and g.location == "Floor"],
        total_plays=sum(g.total_plays or 0 for g in games),
        total_revenue=sum(g.total_revenue or 0.0 for g in games),
        open_orders=open_orders[:6],
        open_order_count=len(open_orders),
        recent_records=recent,
        low_stock=low,
        stale_labels=[g for g in games if g.report_label_stale],
    )
    return _for_role(d, user, open_orders)


def _for_role(d: Dashboard, user, open_orders: list[MaintenanceRecord]) -> Dashboard:
    if user is None:
        return d
    for role in ("admin", "manager", "operator"):
        if user.has_role(role):
            d.role = role
            break
    if d.role == "readonly":
        return d
    d.down_since = _down_since(d.down_games)
    from app.services.inventory_due import due

    d.inventory = due(shopping=d.role != "operator")
    if d.role == "operator":
        d.mine = _mine(user, open_orders)
        theirs_first = sorted(open_orders, key=lambda o: o.id not in d.mine)    # stable
        d.open_orders = theirs_first[:6]
        d.pm_orders = [o for o in open_orders if o.source == "pm"][:6]
        return d
    from app.services.collection import local_today
    from app.services.rotation import plan

    today = local_today()
    d.plays_week, d.value_week = _plays_between(today - dt.timedelta(days=6), today)
    d.plays_last_week, _ = _plays_between(today - dt.timedelta(days=13),
                                          today - dt.timedelta(days=7))
    d.swaps = plan().swaps[:3]
    if d.role == "admin":
        from app.models import Alert

        week_ago = datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=7)
        d.failed_alerts = Alert.query.filter(Alert.status == "failed",
                                             Alert.created_at >= week_ago).count()
    return d
