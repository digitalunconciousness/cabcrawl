"""Application models — re-exported for convenient imports.

Usage::

    from app.models import User, Game, PlayRecord
"""

from app.models.alerts import Alert, AlertChannel, Setting
from app.models.device import Device
from app.models.game import Game, PlayRecord
from app.models.history import GameEvent
from app.models.indexes import attach as _attach_indexes
from app.models.inventory import (
    InventoryItem,
    InventoryRequest,
    InventoryRequestHistory,
    LowStockAlert,
    MaintenanceInventoryUsage,
    StockHistory,
    item_game_compatibility,
)
from app.models.library import LibraryRecord, LibraryState
from app.models.maintenance import MaintenanceRecord, WorkLog
from app.models.parts import (
    ItemSupplier,
    PartCategory,
    PartReservation,
    PriceChange,
    PurchaseOrder,
    PurchaseOrderLine,
    SerialUnit,
    StockCount,
    StockCountLine,
    Supplier,
    TaskPart,
    UnitEvent,
)
from app.models.rail import RailSession, RailSessionTag, Reading
from app.models.rom import BURN_STATES, RomBurn, RomDump, RomFile
from app.models.stock import StockLevel, StockPlace
from app.models.upkeep import MaintenanceTask
from app.models.user import User

_attach_indexes()

__all__ = [
    "User",
    "Game",
    "PlayRecord",
    "MaintenanceRecord",
    "WorkLog",
    "InventoryItem",
    "StockHistory",
    "StockPlace",
    "StockLevel",
    "PartCategory",
    "Supplier",
    "ItemSupplier",
    "PriceChange",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "SerialUnit",
    "UnitEvent",
    "StockCount",
    "StockCountLine",
    "PartReservation",
    "TaskPart",
    "LowStockAlert",
    "MaintenanceInventoryUsage",
    "InventoryRequest",
    "InventoryRequestHistory",
    "item_game_compatibility",
    "Device",
    "BURN_STATES",
    "RailSession",
    "RailSessionTag",
    "RomBurn",
    "RomDump",
    "RomFile",
    "LibraryRecord",
    "LibraryState",
    "Reading",
    "GameEvent",
    "MaintenanceTask",
    "Alert",
    "AlertChannel",
    "Setting",
]
