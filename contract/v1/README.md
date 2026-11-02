# GATBOX → hub contract, v1

The wire format between GATBOX (a Raspberry Pi running the bench diagnostics) and a hub, an
application it reports to. **One direction only: GATBOX pushes, the hub never connects to
GATBOX.**

This directory is owned by GATBOX (since 2026-10-28; the first hub owned it before) at
`docs/contract/v1/`, and vendored byte-identical into each hub at `contract/v1/`. Every copy is
checked against `CHECKSUMS` by a test in its repository, which also compares it with any sibling
checkout it finds, so the copies cannot drift apart without something going red. A change is
made in GATBOX's copy and lands in every repository together.

## The examples are the schema

GATBOX is stdlib-only and neither side carries a JSON Schema validator, so the files under
`examples/` are the normative description. The hub's tests POST `examples/ingest-request.json`
and compare the reply byte-for-byte against `examples/ingest-response.json`, then post it a
second time and compare against `examples/ingest-response-duplicate.json`. GATBOX's tests
build a payload from its own report JSON and compare it against the same request example.

`examples/roster-response.json`, `examples/orders-response.json` and
`examples/session-tag-request.json` are illustrative: the tests assert their keys and types
rather than exact bytes, because their content depends on what is on the floor — and in the
tag's case on an id the hub assigns, which cannot be fixed in advance.

## Endpoints

All paths are relative to the hub's base URL. Every response is
`application/json; charset=utf-8`.

| method | path | auth |
|---|---|---|
| GET | `/api/v1/health` | none |
| POST | `/api/v1/ingest` | device token |
| GET | `/api/v1/roster` | device token |
| GET | `/api/v1/machines/<slug>/orders?status=open` | device token |

### Errors

Always `{"error": "<message>"}` with a meaningful status. `400` malformed or over a limit,
`401` bad or missing token, `404` unknown path or machine, `405` wrong method, `413` body too
large, `415` wrong content type, `429` rate limited, `500` unhandled.

An error status means **nothing was written**. Ingest is all-or-nothing at the request level;
per-item outcomes only appear in a `200`.

## Authentication

```
Authorization: Bearer gbx_<public_id>.<secret>
```

`public_id` is 12 lowercase hex characters; `secret` is 43 characters of URL-safe base64. The
hub stores only a hash of the secret, so the public id is what it looks the device up by. A
token is shown once, when `scripts/create_device.py` mints it.

`GET /api/v1/health` takes no token on purpose: GATBOX uses it to decide whether the LAN
address or the tunnel is reachable, which it needs to know before it commits to a request.

## Additions (all optional)

v1's request shapes never change. These are extras a hub may serve (added 2026-10-13); a side
that doesn't know one ignores it.

- **Health:** `app`, a short name for the hub's software, and `capabilities`, a list of strings
  naming the optional parts it serves. A hub that leaves `capabilities` out has none. GATBOX
  sends a hub only what its last health answer lists. Today there is one capability:
  - `"jobs"`: the hub keeps customer jobs. Its orders also carry `work_order` (the hub's
    document number, a string) and `nte` (a not-to-exceed amount as a decimal string, or
    `null`), and it accepts `phase` (below).
- **Roster:** each machine carries `model_key`, the game it is, as a slug shared with GATBOX's
  game library, or `null` when the hub doesn't know. It is in the example, so a hub with no
  model keys yet sends `null`.
- **`phase`** on a `rail_session` and a `session_tag`: one of `as_found`, `during` and
  `as_left`. GATBOX sends it only to a hub whose health lists `"jobs"`, and leaves it out rather
  than sending `null`. A jobs hub rejects any other value.

## Machines a device may use

Added 2026-10-26; additive, so a device set up before then behaves as it always did.

A device is allowed either **every machine** (the default, and what every device had before)
or **a list of machines**, chosen on the hub's Admin, Devices page. A device limited to a list:

- sees only those machines in `GET /api/v1/roster`;
- gets `404` from `GET /api/v1/machines/<slug>/orders` for any other machine, the same answer
  as for an unknown slug, so it can't learn what else is on the floor;
- has a `rail_session` or `order` for any other machine `rejected` with
  `"machine '<slug>' is not on this device's list"`;
- has a `session_tag` `rejected` when the order isn't for one of its machines (an order with no
  machine is not one of them).

Changing a device's list takes effect on its next request. Refusals for a machine off the list
never clear on retry: the fix is the list, or the Pi's machine setting.

## Identity and idempotency

Every item carries a `uid` that GATBOX computes. Re-sending an item is harmless: the hub
reports `duplicate` and changes nothing. The uid rules are **part of the contract**:

```
rail_session  sha256(f"{public_id}|rail_session|{file}").hexdigest()[:32]
reading       sha256(f"{public_id}|reading|{file}|{epoch:.3f}").hexdigest()[:32]
order         uuid4().hex                 — an order has no natural key
session_tag   sha256(f"{public_id}|session_tag|{order}|{rail_session}").hexdigest()[:32]
```

A `session_tag`'s uid is derivable from the item alone, and it is **not** what the hub dedupes
on: the `(order, rail_session)` pair is a natural key, so re-sending with a freshly minted uid
is still `duplicate`. The uid is there because every item carries one.

`file` is the CSV's base name, e.g. `rail_20260925_021402.csv`. It is the only identity a
session has on the Pi, and it is local wall-clock time with no zone, so it is not unique
across devices — scoping the hash by `public_id` is what makes it so.

**`{epoch:.3f}` is exact.** Three decimals, always, even for a whole number. Formatting the
same reading as `.6f` would produce a different uid and silently duplicate a trace. At ~2
samples per second, milliseconds are unambiguous.

### Only completed sessions may be sent

A session is complete when its file is no longer the live one (not named by
`/run/gatbox/current`). This is not a preference. The trace is downsampled, and a partial
session buckets differently from the finished one, so the same reading would hash to a
different uid in each — the duplicate check would miss and the trace would be stored twice.

## Time

`epoch` — a float of UTC seconds — is authoritative everywhere. The CSV's `iso_time` column is
local wall clock with no offset and is never sent.

Each session also carries `clock`, because the Pi knows when it cannot be trusted:

| `clock.source` | meaning |
|---|---|
| `ntp` | synchronised at session start; absolute times are good |
| `rtc` | from the battery-backed clock, last set from NTP at `clock.ntp_from` |
| `unverified` | no NTP had been seen; **absolute times may be wrong** |

Readings carry `up` (the Pi's monotonic uptime in seconds) alongside `epoch`. Within a session,
`up` is correct even when the wall clock is not, so it is what relative timing should use.

## Limits

| | |
|---|---|
| items per request | 100 |
| readings per session | 2000 |
| readings per request | 20000 |
| request body | 8 MB |
| `order.issue` | 2000 characters |
| `order.technician` | 50 characters |
| `session_tag.note` | 2000 characters |
| `rail_session.file` | 64 characters |

Exceeding any of the first four is a `400` and writes nothing. GATBOX decides how to batch
within them. The text lengths (added 2026-10-26) are per item: an item over one is `rejected`
with the reason, and the rest of the request goes through.
2000 points is what `GET /api/rail/samples/<name>?max=2000` on the Pi produces: its downsampler
keeps the minimum and maximum of each bucket and never discards a `spike`, an `alarm` or an
out-of-range sample, so the shape of the trace survives.

## Item kinds

### `rail_session`

The hub stores what GATBOX computed; it does not recompute statistics. `machine` must be a
slug the hub knows — an unknown one is `rejected`, never created.

`verdict.state` is one of `held`, `left`, `over`, or `null` when the profile has no window
(bench and free profiles have no pass/fail). GATBOX is the only place this is decided.

`window`, `alarm_hi` and `powered` may be `null` together for a bench profile.

### `order`

A maintenance order raised at the bench. `rail_session` optionally links it to the session
that prompted it, by that session's uid. The hub records it with `source: "gatbox"` and the
`uid` as its `external_id`.

`priority` is one of `Low`, `Medium`, `High`, `Critical` and `Urgent`, or left out (then
`Medium`). Each hub maps the five onto its own scale: a hub with four levels stores `Urgent` as
its top one. A hub may reject any other value, with the reason, and the rest of the request goes
through; GATBOX never sends one. The orders read returns the hub's own value.

**Send the session before an order that names it.** An order whose `rail_session` the hub has
not seen is `rejected` rather than stored with the link quietly dropped — a link that silently
disappears is worse than a retry, and both items being idempotent makes the retry free.

### `session_tag`

A trace attached to an order that already exists. The case it is for: a machine has an order
open, someone meters it again at the bench, and the second trace belongs on that order rather
than on a duplicate nobody asked for.

```json
{"kind": "session_tag", "uid": "…", "order": 412,
 "rail_session": "<session uid>", "created": 1790000500.0, "note": "second pass, cold"}
```

`order` is the hub's own id for the record, as returned by
`GET /api/v1/machines/<slug>/orders`. `note` is optional.

It does **not** touch `maintenance_record.rail_session_id`, which keeps its meaning: the
session that prompted the order. A tag is a trace measured afterwards, and the two are stored
and displayed separately.

**Rejected** for an unknown `order`, for a `rail_session` the hub has not ingested (send the
session first, as for an order), and for a session belonging to a different machine than the
order — that last one is almost certainly a bug on the Pi, and filing one machine's trace
against another's order would be worse than a refusal.

**Accepted** for an order that has since been closed. GATBOX tags from a cache that can be
minutes stale, and a rejection that can never clear is a retry loop rather than a diagnosis.

## Response

```json
{
  "contract": "v1",
  "received": 2,
  "results": [
    {"uid": "…", "kind": "rail_session", "status": "created",
     "readings": {"created": 3, "duplicate": 0}},
    {"uid": "…", "kind": "order", "status": "duplicate"}
  ]
}
```

`status` is `created`, `duplicate`, or `rejected` with a `reason`. Results are in the order the
items were sent. The response carries no timestamps, so it is reproducible and can be compared
against the examples exactly.
