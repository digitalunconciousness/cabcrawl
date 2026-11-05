"""Reading the library copy: what the pages show (contract/library-v1/ describes the records).

Everything here reads ``library_record``; nothing writes it (the pull job, app/services/library_pull.py, does).
A machine's game comes from ``Game.model_key``; a game the archive retired (``replaced_by``) is followed to its
survivor, as the contract asks.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models import Game, LibraryRecord, LibraryState
from app.services import library_files

KEY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
KEY_MAX = 60
DOC_ORDER = ("manual", "schematics", "parts", "kit", "bulletin", "other", "yours")
GROUP_ORDER = ("fuse", "monitor", "psu", "power", "other")
GROUP_LABEL = {"fuse": "Fuses", "monitor": "Monitor", "psu": "Power supply", "power": "Power", "other": "Other"}
KIND_LABEL = {"video": "Video", "pinball": "Pinball", "other": "Other"}


def configured() -> bool:
    """Whether an archive is set up for this application. Without one there is no library section anywhere."""
    return bool(current_app.config.get("LIBRARY_URL") and current_app.config.get("LIBRARY_TOKEN"))


def valid_key(key: str) -> bool:
    return bool(key) and len(key) <= KEY_MAX and bool(KEY.match(key)) and key != "unassigned"


def state() -> LibraryState | None:
    return db.session.get(LibraryState, 1)


def refreshed() -> datetime | None:
    s = state()
    return s.last_ok if s else None


def ago(moment: datetime | None) -> str:
    """'12 minutes ago', for a naive-UTC moment."""
    if moment is None:
        return "not yet"
    secs = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - moment).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{round(secs / 60)} minutes ago"
    if secs < 129600:
        return f"{round(secs / 3600)} hours ago"
    return f"{round(secs / 86400)} days ago"


def _held(key: str) -> dict:
    """{kind: [records]} for one game key, live records only."""
    out: dict[str, list] = {}
    for r in LibraryRecord.query.filter_by(model_key=key).order_by(LibraryRecord.rid).all():
        out.setdefault(r.kind, []).append(r)
    return out


def resolve(key: str) -> str | None:
    """The key a machine's key stands for now: a retired game's survivor (a few hops at most), else the key if held."""
    for _ in range(8):
        rec = LibraryRecord.query.filter_by(kind="game", rid=key).first()
        if rec is None:
            return None
        nxt = (rec.data or {}).get("replaced_by")
        if not nxt:
            return key
        key = nxt
    return None


def game(key: str) -> dict | None:
    """Everything held for a game, shaped for the pages; None when the library hasn't this key (yet)."""
    live = resolve(key) if key else None
    if live is None:
        return None
    held = _held(live)
    head = next(iter(held.get("game", [])), None)
    if head is None:
        return None
    d = head.data
    docs = []
    for r in held.get("document", []):
        x = r.data
        docs.append({"id": x["doc_id"], "title": x["title"], "kind": x["kind"], "pages": x.get("pages"), "bytes": x.get("bytes"),
                     "source": x.get("source") or "", "sha256": x.get("sha256"),
                     "file": bool(x.get("sha256")) and library_files.held(x["sha256"])})
    docs.sort(key=lambda x: (DOC_ORDER.index(x["kind"]) if x["kind"] in DOC_ORDER else len(DOC_ORDER), x["title"].lower()))
    titles = {x["id"]: x for x in docs}

    def source(x):
        doc = titles.get(x.get("doc_id"))
        return {"doc": doc, "page": x.get("page")} if doc else None

    limits = [{"rail": r.data["rail"], "lo": r.data["lo"], "hi": r.data["hi"], "quote": r.data.get("quote") or "",
               "confirmed": r.data.get("confirmed"), "source": source(r.data)} for r in held.get("rail_limit", [])]
    limits.sort(key=lambda x: x["rail"])
    facts = [{"group": r.data["group"], "what": r.data["what"], "value": r.data.get("value") or "", "quote": r.data.get("quote") or "",
              "source": source(r.data)} for r in held.get("fact", [])]
    facts.sort(key=lambda x: (GROUP_ORDER.index(x["group"]) if x["group"] in GROUP_ORDER else len(GROUP_ORDER), x["what"].lower()))
    mame = next((r.data for r in held.get("mame", [])), None)
    rom_list = next((r.data for r in held.get("rom_list", [])), None)
    images = sorted(({"label": r.data.get("label") or r.data.get("chip") or r.rid[:8], "chip": r.data.get("chip") or "", "size": r.data["size"],
                      "sha1": r.data["sha1"], "verdict": r.data.get("verdict") or "unknown", "note": r.data.get("note") or "",
                      "revision": r.data.get("revision") or ""} for r in held.get("rom_image", [])), key=lambda x: x["label"].lower())
    return {"key": live, "asked": key, "title": d["title"], "mfr": d.get("mfr") or "", "year": d.get("year"), "kind": d.get("kind"),
            "notes": d.get("notes") or "", "documents": docs, "limits": limits, "facts": facts, "mame": mame, "rom_list": rom_list,
            "images": images, "refreshed": refreshed()}


def for_machine(machine: Game) -> dict | None:
    """The game section of a machine's page: None when there's nothing to show (no archive, no key, or the key isn't held yet)."""
    if not configured() or not machine.model_key:
        return None
    return game(machine.model_key)


def listing() -> list[dict]:
    """Every held game, by title, with what it holds and the machines that name it."""
    games = LibraryRecord.query.filter_by(kind="game").all()
    counts: dict[str, dict[str, int]] = {}
    for kind, key, n in db.session.query(LibraryRecord.kind, LibraryRecord.model_key, db.func.count(LibraryRecord.id)).group_by(
            LibraryRecord.kind, LibraryRecord.model_key):
        counts.setdefault(key, {})[kind] = n
    docs_held = {}
    for r in LibraryRecord.query.filter_by(kind="document").all():
        sha = r.data.get("sha256")
        docs_held.setdefault(r.model_key, []).append(bool(sha) and library_files.held(sha))
    machines: dict[str, list[Game]] = {}
    for m in Game.query.filter(Game.model_key.isnot(None)).order_by(Game.name).all():
        machines.setdefault(resolve(m.model_key) or m.model_key, []).append(m)
    out = []
    for r in games:
        d = r.data
        if d.get("replaced_by"):
            continue
        c = counts.get(r.rid, {})
        have = docs_held.get(r.rid, [])
        out.append({"key": r.rid, "title": d["title"], "mfr": d.get("mfr") or "", "year": d.get("year"), "kind": d.get("kind"),
                    "documents": c.get("document", 0), "manuals_here": sum(1 for h in have if h), "limits": c.get("rail_limit", 0),
                    "confirmed": sum(1 for x in LibraryRecord.query.filter_by(kind="rail_limit", model_key=r.rid).all() if x.data.get("confirmed")),
                    "facts": c.get("fact", 0), "machines": machines.get(r.rid, [])})
    out.sort(key=lambda g: g["title"].lower())
    return out


def key_report() -> dict:
    """Every key a machine names, sorted by what the archive has said about it, for a person at the console:
    held (the archive sent the game), missing (read from the start and the archive has no game with that key: a typo, or
    a game nobody has added there), unread (not read yet; the next pull asks) and invalid (not a game key at all, so
    never asked for). Each is {key: [the names of the machines that name it]}."""
    st = state()
    read = set(st.keys_synced or []) if st else set()
    named: dict[str, list[str]] = {}
    for m in Game.query.filter(Game.model_key.isnot(None)).order_by(Game.name).all():
        named.setdefault(m.model_key, []).append(m.name)
    out: dict[str, dict[str, list[str]]] = {"held": {}, "missing": {}, "unread": {}, "invalid": {}}
    for key, names in sorted(named.items()):
        if not valid_key(key):
            out["invalid"][key] = names
        elif game(key) is not None:
            out["held"][key] = names
        elif key in read:
            out["missing"][key] = names
        else:
            out["unread"][key] = names
    return out


def key_report_lines(report: dict, limit: int = 20) -> list[str]:
    """key_report() as the lines `pull_library.py --status` prints under its first line."""
    total = sum(len(v) for v in report.values())
    if not total:
        return ["no machine names a game yet (a manager links one on the machine's page)"]

    def group(title: str, rows: dict) -> list[str]:
        if not rows:
            return []
        lines = [f"  {title}"]
        for key, names in list(rows.items())[:limit]:
            lines.append(f"    {key}  <-  {', '.join(names[:4])}" + (f" and {len(names) - 4} more" if len(names) > 4 else ""))
        if len(rows) > limit:
            lines.append(f"    ... and {len(rows) - limit} more")
        return lines

    head = (f"keys: {total} named by machines: {len(report['held'])} held, {len(report['missing'])} not at the archive, "
            f"{len(report['unread'])} not read yet" + (f", {len(report['invalid'])} not game keys" if report["invalid"] else ""))
    return ([head]
            + group("NOT AT THE ARCHIVE (read in full, and it has no game with that key: fix the key here, or add the game there)", report["missing"])
            + group("not read yet (the next pull asks for these)", report["unread"])
            + group("not game keys (lowercase words joined by hyphens; these are never asked for)", report["invalid"]))


def clear(key: str) -> int:
    """A manager clears a game no machine names any more: its records go, and so do manuals only it held. -> rows removed."""
    docs = LibraryRecord.query.filter_by(model_key=key, kind="document").all()
    shas = {r.data.get("sha256") for r in docs if r.data.get("sha256")}
    n = LibraryRecord.query.filter_by(model_key=key).delete()
    forget_unnamed(shas)
    st = state()
    if st is not None and key in (st.keys_synced or []):
        st.keys_synced = [k for k in st.keys_synced if k != key]      # a machine that names it later has it read from the start again
    return n


def forget_unnamed(shas: set) -> None:
    """Remove the manuals among these hashes that no held document names any more."""
    shas = {s for s in shas if s}
    if shas:
        still = {(r.data or {}).get("sha256") for r in LibraryRecord.query.filter_by(kind="document").all()}
        for sha in shas - still:
            library_files.forget(sha)
