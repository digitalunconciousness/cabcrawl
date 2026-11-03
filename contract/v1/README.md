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

The ROM-image part (capability `roms`, added 2026-11-03) works the same way. A hub that lists
`roms` runs `examples/rom-image.json` (32 invented bytes, as hex, with their hashes) through
`files-begin-request.json`, one part and a finish, comparing each reply with the matching
`files-*-response.json`; then posts `rom-ingest-request.json` twice and compares the replies
with `rom-ingest-response.json` and `rom-ingest-response-duplicate.json`.
`machine-dumps-response.json` is illustrative. The two ROM uids hash the machine's slug
(`rom_dump` and `burn`, below), so a hub whose test machine is not called `widget-wars` (its
machines have tags of its own) substitutes its slug and recomputes those two uids by the rule,
and expects the replies to carry the recomputed uids.

## Endpoints

All paths are relative to the hub's base URL. Every response is
`application/json; charset=utf-8`.

| method | path | auth |
|---|---|---|
| GET | `/api/v1/health` | none |
| POST | `/api/v1/ingest` | device token |
| GET | `/api/v1/roster` | device token |
| GET | `/api/v1/machines/<slug>/orders?status=open` | device token |
| POST | `/api/v1/files` | device token (capability `roms`) |
| PUT | `/api/v1/files/<sha256>/parts/<n>` | device token (capability `roms`) |
| POST | `/api/v1/files/<sha256>/finish` | device token (capability `roms`) |
| GET | `/api/v1/files/<sha256>` | device token (capability `roms`) |
| GET | `/api/v1/machines/<slug>/dumps` | device token (capability `roms`) |

### Errors

Always `{"error": "<message>"}` with a meaningful status. `400` malformed or over a limit,
`401` bad or missing token, `404` unknown path or machine, `405` wrong method, `413` body too
large, `415` wrong content type, `429` rate limited, `500` unhandled. The files calls add `409`
(a finish with parts missing) and `422` (a finished upload whose bytes are not what was
announced).

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
  sends a hub only what its last health answer lists. Today there are two capabilities:
  - `"jobs"`: the hub keeps customer jobs. Its orders also carry `work_order` (the hub's
    document number, a string) and `nte` (a not-to-exceed amount as a decimal string, or
    `null`), and it accepts `phase` (below).
  - `"roms"` (added 2026-11-03): the hub keeps the ROM images GATBOX dumps and burns. It serves
    the files calls and accepts `rom_dump` and `burn` items (below).
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
  machine is not one of them);
- has a `rom_dump` or `burn` for any other machine `rejected` with the same reason, and gets
  `404` from `GET /api/v1/machines/<slug>/dumps` for it.

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
rom_dump      sha256(f"{public_id}|rom_dump|{machine}|{label}|{sha1}").hexdigest()[:32]
burn          sha256(f"{public_id}|burn|{machine}|{image_sha1}|{created:.3f}").hexdigest()[:32]
```

A `session_tag`'s uid is derivable from the item alone, and it is **not** what the hub dedupes
on: the `(order, rail_session)` pair is a natural key, so re-sending with a freshly minted uid
is still `duplicate`. The uid is there because every item carries one.

`file` is the CSV's base name, e.g. `rail_20260925_021402.csv`. It is the only identity a
session has on the Pi, and it is local wall-clock time with no zone, so it is not unique
across devices — scoping the hash by `public_id` is what makes it so.

**`{epoch:.3f}` is exact.** Three decimals, always, even for a whole number. Formatting the
same reading as `.6f` would produce a different uid and silently duplicate a trace. At ~2
samples per second, milliseconds are unambiguous. A burn's `{created:.3f}` is the same rule: the
epoch of its line in GATBOX's burn log, which is the only identity a burn has.

A `rom_dump` is the same dump when the machine, the chip's label and the SHA-1 are the same,
whenever it was read, which is how GATBOX's own archive treats it.

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
| a file (`roms`) | 32 MiB (33554432 bytes), in parts of 8 MiB (8388608 bytes) |
| `rom_dump.label` | 40 characters |
| `rom_dump.part`, `burn.part` | 64 characters |
| `burn.error` | 2000 characters |

Exceeding any of the first four is a `400` and writes nothing. GATBOX decides how to batch
within them. The text lengths (added 2026-10-26) are per item: an item over one is `rejected`
with the reason, and the rest of the request goes through.
2000 points is what `GET /api/rail/samples/<name>?max=2000` on the Pi produces: its downsampler
keeps the minimum and maximum of each bucket and never discards a `spike`, an `alarm` or an
out-of-range sample, so the shape of the trace survives.

## Files (capability `roms`)

A ROM image is a file. It goes **before** the item that names it, and in parts, so an upload
that is cut off resumes where it stopped. A file is named by the SHA-256 of its bytes: the same
bytes are the same file whoever sends them, and a hub keeps one copy. Every call is idempotent.

```
POST /api/v1/files                       {"sha256": "<64 hex>", "size": 32}
PUT  /api/v1/files/<sha256>/parts/<n>    the part's bytes, application/octet-stream
POST /api/v1/files/<sha256>/finish       no body
GET  /api/v1/files/<sha256>              the bytes, Range supported
```

- **Begin** answers `{"state": "stored", …}` when the hub already holds the file (send nothing),
  and otherwise `{"state": "open", "part_size": 8388608, "parts": N, "have": [0, …]}`: how the
  file is cut and which parts the hub already has, so a resumed upload sends only the rest. A
  `size` over the limit is a `413`; a malformed one a `400`.
- **A part** is numbered from 0. Every part is exactly `part_size` bytes except the last, which
  is the remainder. A wrong length or a number outside `0..parts-1` is a `400`; a file that was
  never begun is a `404`; a file already stored answers `{"state": "stored"}` and ignores the
  bytes. A part the hub already holds is replaced by the same bytes. The answer is
  `{"state": "open", "part": n, "have": […]}`.
- **Finish** joins the parts and checks the size and the SHA-256. Parts missing is a `409` naming
  them; a join that is not what was announced is a `422` and **the parts are dropped**: the file
  has to be sent again. Otherwise `{"state": "stored", …}`, and again on every later call.
- **Who can read a file.** People, behind the hub's own login; never a public address. A device
  reads one only through `GET /api/v1/files/<sha256>`, and only when a `rom_dump` or `burn` for a
  machine on its list names it; any other file is a `404`.
- Half-sent uploads older than 7 days may be dropped. Sending the file again just begins again.

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

### `rom_dump` (capability `roms`)

One chip read twice on GATBOX's programmer, with the two reads identical, and archived on the
Pi. The file (the image) is sent first and named here by its SHA-256; the hub stores what GATBOX
computed and **checks the file against it**:

```json
{"kind": "rom_dump", "uid": "…", "machine": "widget-wars", "label": "U10", "part": "27C020@DIP32",
 "size": 32, "crc32": "91267e8a", "sha1": "…", "sha256": "…",
 "romident": {"match": true, "matches": [{"set": "widgetw", "rom": "ww-u10.bin"}]},
 "reads": 2, "identical": true, "blank": false, "created": 1790000300.0,
 "clock": {"source": "ntp"}}
```

`label` is the chip's name on the board; `part` is the programmer's part name. `romident` is
what `mame -romident` said: `{"match": true, "matches": […]}` (each with a `set` and a `rom`,
and optionally a `description`), `{"match": false}`, or `null` when MAME did not run. `clock.source`
is as for a session, or `unknown`. `crc32` is 8 lower-case hex digits, `sha1` 40, `sha256` 64.
`order` (the hub's own id, as `GET /machines/<slug>/orders` returns it) is optional: the order
this dump was made for. Left out rather than sent as `null`.

**Rejected** for an unknown machine; for a machine off the device's list; for a file the hub does
not hold (send the file first, as for a session before its order); for a `size`, `sha1` or `crc32`
that is not what the stored file computes to; for `identical` other than `true` or `reads` under 2;
for an `order` that is not an order of that machine; and, where orders need a switch the device
does not have, for any `order` at all, with the same reason an `order` item gets.

### `burn` (capability `roms`)

One attempt to write an image to a chip, **verified or stopped**. A stopped burn is sent too: the
burn that went wrong is the record that matters.

```json
{"kind": "burn", "uid": "…", "machine": "widget-wars", "image": "<sha256>", "image_sha1": "…",
 "part": "27C020@DIP32", "size": 32, "state": "verified", "vpp": "13V", "blank_ok": true,
 "error": null, "created": 1790000400.0}
```

`image` is the SHA-256 of the file that was burned, and the file is sent first, even when it was
never a dump of this machine. `state` is `verified` (written, and two read-backs identical to the
image) or `stopped`, with the reason in `error`. `vpp` is the programming voltage as the
programmer prints it, or `null`; `blank_ok` says whether the blank check passed, or `null` when
it did not run. `order` is optional, as for `rom_dump`. **Rejected** on the same grounds, and for
a `size` or `image_sha1` that is not the stored file's.

### Read back

`GET /api/v1/machines/<slug>/dumps` returns `{"machine": …, "dumps": […], "burns": […]}`: the
items above as the hub holds them, each with its `uid`, newest first. It exists so a rebuilt
GATBOX can find its machines' dumps and burns again; the images come back through
`GET /api/v1/files/<sha256>`. It is `404` for a machine the device may not use or the hub does
not have.

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
