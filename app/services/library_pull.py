"""The pull job: keep a read-only copy of the games this floor's machines name (contract/library-v1/).

``scripts/pull_library.py`` runs this on a timer. It asks the archive for what changed, for its own keys only
(``GET /api/v1/library/changes?since=&limit=&models=``, a reader token), replaces its copy of each record with
the one it is sent, removes tombstones, and fetches each document's PDF it doesn't hold, in 8 MiB parts that
resume. It never writes to the archive and sends nothing but the token; it never merges, because nothing here
edits what it holds.

* A key a machine gains is read from cursor 0 once (``library_state.keys_synced``); the keys already held are
  read from the saved cursor. A key no machine names any more stops refreshing and is kept until a manager
  clears it.
* A retired game (``replaced_by``) is followed: its survivor's key is asked for too.
* A cursor above the archive's ``head`` means it was restored from an older copy: every key is read again from 0,
  and once that read has finished whatever the restored archive no longer has is dropped from the copy.
* Out of reach keeps what is held and says so in ``library_state.error``. The archive refusing the token
  (``refused``) is the one answer a person has to act on.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models import Game, LibraryRecord, LibraryState
from app.services import library, library_files

USER_AGENT = "cabcrawl-library/1 (+https://github.com/digitalunconciousness/cabcrawl)"
KINDS = ("game", "document", "mame", "rom_list", "rom_image", "rail_limit", "fact")
PAGE = 500                     # records a request, the archive's limit
KEYS_PER_REQUEST = 500         # `models=` takes at most this many keys
PART = 8 * 1024 * 1024
MAX_PAGES = 200
FILES_PER_RUN = 20
TIMEOUT = 60.0
BUDGET = 240.0


class Unreachable(Exception):
    """The archive didn't answer, or answered something this job can't use: what is held stays."""


class Refused(Exception):
    """The archive said no to the token (HTTP 401 or 403): only a person can fix that."""


class Restored(Exception):
    """The archive's head is below the saved cursor: it was restored from an older copy."""


class Cut(Unreachable):
    """An answer that stopped before its end. ``partial`` is the prefix that arrived: a manual's part resumes from it."""

    def __init__(self, status: int, partial: bytes):
        super().__init__("the archive's answer was cut off")
        self.status, self.partial = status, partial


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would send the token (and the Access headers) on to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class Client:
    def __init__(self, url: str, token: str, access_id: str = "", access_secret: str = "", timeout: float = TIMEOUT):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
        if access_id and access_secret:
            self.headers["CF-Access-Client-Id"], self.headers["CF-Access-Client-Secret"] = access_id, access_secret

    def get(self, path: str, extra: dict | None = None) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self.url + path, headers={**self.headers, **(extra or {})}, method="GET")
        try:
            with OPENER.open(req, timeout=self.timeout) as r:
                try:
                    return r.status, r.read(), dict(r.headers)
                except http.client.IncompleteRead as e:
                    raise Cut(r.status, bytes(e.partial)) from None
        except urllib.error.HTTPError as e:
            status, body, headers = e.code, e.read(), dict(e.headers)
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as e:
            raise Unreachable(f"{self.url}: {e}") from None
        if status in (401, 403):
            raise Refused(f"the archive refused the token (HTTP {status})")
        if 300 <= status < 400:
            where = urllib.parse.urlsplit({k.lower(): v for k, v in headers.items()}.get("location") or "").netloc
            raise Unreachable(
                f"the archive sent us to {where or 'somewhere else'} instead of answering (HTTP {status}); "
                "the token is not sent on. Is an Access policy asking for a login?"
            )
        return status, body, headers

    def changes(self, since: int, models: list[str]) -> dict:
        q = urllib.parse.urlencode({"since": since, "limit": PAGE, "models": ",".join(models)}, safe=",")
        status, body, _ = self.get(f"/api/v1/library/changes?{q}")
        if status != 200:
            raise Unreachable(f"the archive answered HTTP {status} for the change feed")
        try:
            page = json.loads(body)
        except ValueError:
            raise Unreachable("the archive's change feed isn't JSON") from None
        if not isinstance(page, dict) or not isinstance(page.get("records"), list) or "cursor" not in page or "more" not in page:
            raise Unreachable("the archive's change feed isn't the shape the contract describes")
        return page


def client() -> Client:
    c = current_app.config
    return Client(c["LIBRARY_URL"], c["LIBRARY_TOKEN"], c.get("LIBRARY_ACCESS_ID", ""), c.get("LIBRARY_ACCESS_SECRET", ""))


def wanted_keys() -> set[str]:
    """The games to ask for: the keys the machines name, and the survivors of retired games already held."""
    keys = {m.model_key for m in Game.query.filter(Game.model_key.isnot(None)).all()}
    for rec in LibraryRecord.query.filter_by(kind="game").all():
        nxt = (rec.data or {}).get("replaced_by")
        if nxt and rec.rid in keys:
            keys.add(nxt)
    return {k for k in keys if library.valid_key(k)}


def apply_records(records: list[dict], seen: set | None = None) -> int:
    """Replace the copy with what the archive sent. -> how many rows changed. Records of a kind this job doesn't know are left alone.

    ``seen`` collects the ``(kind, id)`` of every live record sent, changed or not (a read after a restore uses it to drop the rest)."""
    changed = 0
    for r in records:
        if not isinstance(r, dict) or r.get("kind") not in KINDS or not isinstance(r.get("id"), str) \
                or not isinstance(r.get("rev"), int) or r["rev"] < 1:
            continue
        kind, rid = r["kind"], r["id"]
        row = LibraryRecord.query.filter_by(kind=kind, rid=rid).first()
        if r.get("deleted"):
            if row is not None:
                db.session.delete(row)
                changed += 1
            continue
        data = r.get("data")
        if not isinstance(data, dict):
            continue
        if seen is not None:
            seen.add((kind, rid))
        if row is not None and row.rev == r["rev"] and row.hash == r.get("hash"):
            continue
        if row is None:
            row = LibraryRecord(kind=kind, rid=rid)
            db.session.add(row)
        row.model_key = rid if kind == "game" else data.get("model_key")
        row.rev, row.hash, row.at, row.by, row.data, row.fetched = r["rev"], r.get("hash") or "", r.get("at"), r.get("by"), data, _now()
        changed += 1
    db.session.commit()
    return changed


def _pull(c: Client, keys: list[str], since: int, seen: set | None = None) -> tuple[int, int]:
    """Read the feed for these keys from `since` to its end. -> (the cursor to keep, rows changed)."""
    cursor, changed = since, 0
    for _ in range(MAX_PAGES):
        page = c.changes(cursor, keys)
        head = page.get("head")
        if isinstance(head, int) and head < since:
            raise Restored()
        changed += apply_records(page["records"], seen)
        cursor = page["cursor"]
        if not page["more"]:
            break
    return cursor, changed


def _state() -> LibraryState:
    st = db.session.get(LibraryState, 1)
    if st is None:
        st = LibraryState(id=1, cursor=0, keys_synced=[], refused=False, records=0)
        db.session.add(st)
        db.session.commit()
    return st


def _chunks(items: list[str]):
    for i in range(0, len(items), KEYS_PER_REQUEST):
        yield items[i:i + KEYS_PER_REQUEST]


def read_feed(c: Client, st: LibraryState, summary: dict) -> None:
    """Read what changed for the keys held, and back-fill the keys not read yet. Raises Unreachable / Refused."""
    try:
        _read(c, st, summary, rewind=False)
    except Restored:
        summary["restored"] = True
        _read(c, st, summary, rewind=True)


def _read(c: Client, st: LibraryState, summary: dict, rewind: bool) -> None:
    """One pass. ``rewind`` ignores the saved cursor and keys (the archive was restored): every key is read from 0, and the copy
    is trimmed to what that read found only once it has finished, so a read that dies half way changes nothing it shouldn't."""
    seen: set | None = set() if rewind else None
    held = set() if rewind else set(st.keys_synced or [])
    synced, marks = set(held), []
    for _ in range(5):                                   # a survivor found along the way is asked for in the next round
        new = sorted(wanted_keys() - synced)
        if not new:
            break
        for chunk in _chunks(new):
            cur, n = _pull(c, chunk, 0, seen)
            summary["changed"] += n
            synced |= set(chunk)
            marks.append(cur)
            if not rewind:
                st.keys_synced = sorted(synced)
                db.session.commit()
    for chunk in _chunks(sorted(wanted_keys() & held)):
        cur, n = _pull(c, chunk, st.cursor, seen)        # Restored, if the archive's head is below the cursor
        summary["changed"] += n
        marks.append(cur)
    if rewind:
        _trim(synced, seen)
    if marks:
        st.cursor = min(marks)                           # a lower bound for every key: re-reading a record is harmless
    st.keys_synced = sorted(synced)
    db.session.commit()


def _trim(keys: set, seen: set) -> None:
    """After a full re-read: drop the records of these keys that the (restored) archive didn't send, and manuals nothing names now."""
    gone = [r for r in LibraryRecord.query.filter(LibraryRecord.model_key.in_(keys)).all() if (r.kind, r.rid) not in seen]
    shas = {(r.data or {}).get("sha256") for r in gone if r.kind == "document"}
    for r in gone:
        db.session.delete(r)
    db.session.commit()
    library.forget_unnamed(shas)


def _fetch(c: Client, sha: str, expected: int | None, deadline: float, summary: dict, log) -> None:
    """One manual, from the byte the last attempt reached. A hash that doesn't match is dropped, never kept."""
    limit = current_app.config["LIBRARY_MAX_FILE"]
    part = library_files.part_path(sha)
    offset = part.stat().st_size if part.exists() else 0
    total = None
    while True:
        if time.monotonic() > deadline:
            summary["files_later"] += 1
            return
        try:
            status, body, headers = c.get(f"/api/v1/files/{sha}", {"Range": f"bytes={offset}-{offset + PART - 1}"})
        except Cut as cut:
            if cut.status == 206:                        # what arrived is the front of the part asked for: keep it, resume from there
                with open(part, "ab") as f:
                    f.write(cut.partial)
            elif cut.status == 200:                      # Range ignored: it is the front of the whole file
                part.write_bytes(cut.partial)
            raise
        if status == 404:
            summary["files_absent"] += 1                 # listed, but the archive doesn't hold the file (yet)
            return
        if status == 416 and offset > 0 and (total is None or offset >= total):
            break                                        # what is saved is the whole file (a run died before it was joined): check it
        if status == 200:                                # the archive ignored Range: the whole file
            offset, total = 0, len(body)
            part.write_bytes(b"")
        elif status != 206:
            raise Unreachable(f"the archive answered HTTP {status} for a manual")
        else:
            rng = headers.get("Content-Range") or headers.get("content-range") or ""
            try:
                total = int(rng.rpartition("/")[2])
            except ValueError:
                raise Unreachable("a manual's Content-Range isn't readable") from None
        if total > limit:
            part.unlink(missing_ok=True)
            summary["files_failed"].append({"sha256": sha, "why": f"{total} bytes is over this application's limit"})
            return
        if offset > total:
            part.unlink(missing_ok=True)                 # the file changed under a saved part: start again next run
            return
        with open(part, "ab") as f:
            f.write(body)
        offset += len(body)
        if offset >= total:
            break
        if not body:
            raise Unreachable("the archive sent no bytes for a manual")
    try:
        library_files.finish(sha)
        summary["files"] += 1
    except ValueError as e:
        summary["files_failed"].append({"sha256": sha, "why": str(e)})
        log(f"  manual {sha[:12]}: {e}")


def fetch_files(c: Client, deadline: float, summary: dict, log) -> None:
    todo = []
    for r in LibraryRecord.query.filter_by(kind="document").order_by(LibraryRecord.id).all():
        sha = (r.data or {}).get("sha256")
        if sha and library_files.valid(sha) and not library_files.held(sha) and sha not in {t[0] for t in todo}:
            todo.append((sha, (r.data or {}).get("bytes")))
    summary["files_waiting"] = len(todo)
    for sha, size in todo[:FILES_PER_RUN]:
        _fetch(c, sha, size, deadline, summary, log)


def run(log=print) -> dict:
    """One pull. -> a summary dict; library_state says how it went. Never raises for an archive that is out of reach."""
    summary = {"configured": library.configured(), "changed": 0, "files": 0, "files_absent": 0, "files_later": 0, "files_failed": [],
               "files_waiting": 0, "ok": False, "refused": False, "error": None, "restored": False}
    if not summary["configured"]:
        return summary
    st = _state()
    st.last_try = _now()
    db.session.commit()
    c = client()
    deadline = time.monotonic() + BUDGET
    try:
        read_feed(c, st, summary)
        fetch_files(c, deadline, summary, log)
        st.last_ok, st.error, st.refused = _now(), None, False
        summary["ok"] = True
    except Refused as e:
        st.error, st.refused = str(e), True
        summary.update(refused=True, error=str(e))
    except Unreachable as e:
        st.error, st.refused = str(e)[:500], False
        summary["error"] = str(e)
    st.records = LibraryRecord.query.count()
    db.session.commit()
    return summary
