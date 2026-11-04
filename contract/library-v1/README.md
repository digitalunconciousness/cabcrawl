# The library, read side (v1)

What an app (the barcade's cabcrawl, the shop's Tech Tracker) needs to keep a **read-only copy** of the games its machines
name. The archive owns the library; an app only reads, and nothing an app does is written back or remembered. This
directory is **Gatbox's** and each app vendors it byte-identical at `contract/library-v1/`, beside contract v1, with the
same `CHECKSUMS` check. The whole format (the writes, the merge, the rules the store enforces) is `../README.md`, which
stays Gatbox's own: an app needs only what is here.

## Who may ask

A **reader token**, one per app, minted by the archive's owner (`gatbox-archive token add NAME --role reader`):

    Authorization: Bearer gbx_<public id>.<secret>

It is reached on the archive's private address (reader tokens only, read-only) or through the tunnel, where Cloudflare
Access also wants its own service-token headers (`CF-Access-Client-Id`, `CF-Access-Client-Secret`). A reader may read the
change feed and **manuals** (the documents' PDFs). It may not write, and it is never given a ROM image: asking for one is
the same `404` as asking for a file that doesn't exist. The archive keeps no record of what a reader asked for.

| status | meaning | body |
|---|---|---|
| 401 | no token, or not a valid one | `{"error": "a valid token is required"}` (`examples/error-unauthorized.json`) |
| 404 | no such file, or not one a reader may have | `{"error": "no such file"}` (`examples/error-not-found.json`) |

## The change feed

    GET /api/v1/library/changes?since=<cursor>&limit=<1..500>&models=<key,key,...>

`since` is the cursor the last answer gave (0 to start; an app that has never read, or that is adding a game, starts at 0).
`limit` is at most 500. `models` is a comma-separated list of `model_key`s, at most 500: only those games' records come
back, a game and everything that names it (its documents, MAME set, ROM list, limits, facts and ROM images). Without
`models` a reader gets every record.

    {"records": [ ... ], "cursor": 4, "more": true, "head": 9}

- `records`: the records changed after `since`, oldest first. A record changed twice shows once, at its latest revision.
- `cursor`: ask again with this as `since`. It moves past records the `models` filter skipped.
- `more`: `true` while there is more to read. **A page can be short, even empty, with `more` true** (the filter skipped
  what was there): keep asking until `more` is false (`examples/changes-response-filtered.json`).
- `head`: the newest cursor the library has. **A saved cursor above `head` means the archive was restored from an older
  copy**: start again from 0 and replace what you hold.

`examples/changes-response.json` is a first page, `changes-response-last.json` the last, `changes-response-empty.json` an
answer with nothing new, and `changes-response-all.json` every record of two games at once: one of each kind, a tombstone
among them.

### A record

    {"kind": "fact", "id": "...", "rev": 1, "hash": "...", "deleted": false, "at": 1760000000.0, "by": "...", "data": {...}}

`(kind, id)` names it; `rev` is its revision (it only goes up); `hash` is the SHA-256 of its data's canonical JSON (sorted
keys, no whitespace, UTF-8, NFC), which an app may check or ignore; `at` and `by` say when and by whom it was last written.

**An app replaces its copy of a record with the one it is sent**, whatever it held: there is never a merge, because an app
never edits. A record with `deleted: true` is a tombstone: remove it. A tombstone's `data` is the last version, to be ignored.

| kind | id | data |
|---|---|---|
| `game` | the `model_key` | `title, mfr, year, kind (video\|pinball\|other), notes, replaced_by` |
| `document` | `<model_key>/<doc_id>` | `model_key, doc_id, title, kind, source_url, source, sha256, pages, bytes` |
| `mame` | the `model_key` | `model_key, set, prefer, status (sure\|check\|null), why` (`set` is null: MAME doesn't list the game) |
| `rom_list` | the `model_key` | `model_key, mame, set, versions[{name, parent, description, year, mfr, roms[{name, size, crc, sha1, region, status, bios}], disks[{name, sha1, region, status}]}]`: hashes only, no ROM data |
| `rom_image` | the image's SHA-256 | `size, crc32, sha1, label, part, romident, model_key, chip, verdict (good\|bad\|unknown), revision, note`: a mark, not the file |
| `rail_limit` | 32 hex digits | `model_key, rail, lo, hi, doc_id, page, quote, confirmed (null \| {by, at})`: volts are decimal strings (`"4.75"`) |
| `fact` | 32 hex digits | `model_key, group (fuse\|monitor\|psu\|power\|other), what, value, doc_id, page, quote` |

- **`model_key`**: `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters. One key per game, permanent.
- **A retired game** has `replaced_by: <survivor>`. Follow it: the survivor's records are the game's, and an app should
  add the survivor to the keys it asks for. A game is never deleted.
- **A limit with `confirmed: null`** has been written down from a manual but nobody has checked it against the page. An app
  shows it as that, not as a number to rely on.
- A `document` may have no `sha256` yet (listed, with no file held); a `rom_image` names its game in `model_key` or none.

## Manuals

    GET /api/v1/files/<sha256>            Range: bytes=<first>-<last>

A document's PDF, named by its SHA-256. Ask for at most 8 MiB at a time with `Range`; the answer is `206` with
`Content-Range: bytes <first>-<last>/<total>` (`examples/files-range-response.txt`), `Accept-Ranges: bytes`, an `ETag`
of the hash, and the PDF's type. Without `Range` the whole file comes back with `200`. A range outside the file is `416`.
The bytes hash to the name: **check the SHA-256 of what was joined before keeping it**, and keep a partial download so a
cut-off one can resume from the byte it reached. `examples/manual.pdf.b64` is the file the examples' document names, as base64 (the repositories refuse a `.pdf` file by rule): decode it
and it hashes to the document's `sha256`.

## What an app does, in short

1. Hold the keys its machines name (and the `replaced_by` survivors of retired ones).
2. For a key not yet held, read from cursor 0 with `models=<that key>`; for the keys already held, read from the saved
   cursor with `models=<those keys>`. Page until `more` is false, replacing records and removing tombstones.
3. Fetch each held document's PDF when its `sha256` is set and the file isn't held; check its hash.
4. Save the cursor, keep what was held when the archive can't be reached, and say when it last refreshed.
5. Never write to the archive, and send nothing but the token.
