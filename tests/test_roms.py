"""ROM images from GATBOX (contract v1, capability roms): the resumable upload, the rom_dump
and burn items with every refusal, reading back, the device allow-list, and what people see and
may download. The examples under contract/v1/examples/ are the spec: the first tests run them as
written; the rest bend them one field at a time. Every machine and every byte is invented.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import zlib

import pytest

from app.services import rom_files

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "contract", "v1", "examples")
PUBLIC_ID = "a1b2c3d4e5f6"
MIB = 1024 * 1024


def example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return json.load(fh)


def img():
    return bytes.fromhex(example("rom-image.json")["hex"])


def auth(bench):
    return {"Authorization": f"Bearer {bench['token']}"}


@pytest.fixture()
def bench(app, tmp_path):
    """The contract's device (the public id the examples were made for), its two machines, and
    a scratch folder for the image store."""
    from app.extensions import db
    from app.models import Device, Game

    app.config["ROM_FILES_DIR"] = str(tmp_path / "rom_files")
    dev = Device(name="test-bench")
    dev.public_id = PUBLIC_ID
    token = dev.issue_token()
    db.session.add_all([dev, Game(name="Widget Wars", barcode="widget-wars", status="Working",
                                  location="Floor"),
                        Game(name="Sprocket", barcode="pin-sprocket", status="Working",
                             location="Floor")])
    db.session.commit()
    return {"token": token, "public_id": PUBLIC_ID, "dir": tmp_path / "rom_files"}


def count(table):
    from sqlalchemy import text

    from app.extensions import db
    return db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def post(client, bench, payload):
    return client.post("/api/v1/ingest", json=payload, headers=auth(bench))


def upload(client, bench, data):
    """The whole upload of `data`, in parts: begin, every part, finish. Returns finish's answer."""
    sha = hashlib.sha256(data).hexdigest()
    first = client.post("/api/v1/files", json={"sha256": sha, "size": len(data)},
                        headers=auth(bench))
    assert first.status_code == 200, first.get_data(as_text=True)
    if first.get_json()["state"] == "stored":
        return first
    for n in range(first.get_json()["parts"]):
        chunk = data[n * rom_files.PART_SIZE:(n + 1) * rom_files.PART_SIZE]
        r = client.put(f"/api/v1/files/{sha}/parts/{n}", data=chunk, headers=auth(bench),
                       content_type="application/octet-stream")
        assert r.status_code == 200, r.get_data(as_text=True)
    return client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))


def items():
    return {i["kind"]: i for i in example("rom-ingest-request.json")["items"]}


def sealed(item, public_id=PUBLIC_ID):
    """The item with its uid recomputed by the contract's rule, after a field was changed."""
    item = copy.deepcopy(item)
    if item["kind"] == "rom_dump":
        key = f"{public_id}|rom_dump|{item['machine']}|{item['label']}|{item['sha1']}"
    else:
        key = f"{public_id}|burn|{item['machine']}|{item['image_sha1']}|{item['created']:.3f}"
    item["uid"] = hashlib.sha256(key.encode()).hexdigest()[:32]
    return item


def one(client, bench, item):
    r = post(client, bench, {"contract": "v1", "device": PUBLIC_ID, "items": [item]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["results"][0]


def refused(client, bench, item, text_in_reason):
    r = one(client, bench, item)
    assert r["status"] == "rejected" and text_in_reason in r["reason"], r
    return r


# --- the examples, as written ---------------------------------------------------------------

def test_the_files_examples_run_as_written(client, bench):
    sha = example("rom-image.json")["sha256"]
    begin = client.post("/api/v1/files", json=example("files-begin-request.json"),
                        headers=auth(bench))
    assert begin.status_code == 200 and begin.get_json() == example("files-begin-response.json")
    part = client.put(f"/api/v1/files/{sha}/parts/0", data=img(), headers=auth(bench),
                      content_type="application/octet-stream")
    assert part.get_json() == example("files-part-response.json")
    done = client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))
    assert done.status_code == 200 and done.get_json() == example("files-finish-response.json")
    again = client.post("/api/v1/files", json=example("files-begin-request.json"),
                        headers=auth(bench))
    assert again.get_json() == example("files-begin-response-stored.json")


def test_posting_the_rom_examples_twice_gives_created_then_duplicate(client, bench):
    upload(client, bench, img())
    first = post(client, bench, example("rom-ingest-request.json"))
    assert first.status_code == 200, first.get_data(as_text=True)
    assert first.get_json() == example("rom-ingest-response.json")
    assert (count("rom_dump"), count("rom_burn"), count("rom_file")) == (1, 1, 1)
    second = post(client, bench, example("rom-ingest-request.json"))
    assert second.get_json() == example("rom-ingest-response-duplicate.json")
    assert (count("rom_dump"), count("rom_burn"), count("rom_file")) == (1, 1, 1)


def test_the_hub_says_what_it_speaks(client):
    body = client.get("/api/v1/health").get_json()
    assert body["app"] == "cabcrawl" and body["capabilities"] == ["roms"]


def test_the_stored_dump_is_exactly_what_was_sent(client, bench):
    from app.models import RomBurn, RomDump

    upload(client, bench, img())
    post(client, bench, example("rom-ingest-request.json"))
    d, want = RomDump.query.one(), items()["rom_dump"]
    assert (d.label, d.part, d.size_bytes, d.crc32, d.sha1, d.reads, d.identical, d.blank,
            d.clock) == (want["label"], want["part"], 32, want["crc32"], want["sha1"], 2, True,
                         False, "ntp")
    assert d.romident == want["romident"] and d.taken.timestamp() > 0
    b = RomBurn.query.one()
    assert (b.state, b.vpp, b.blank_ok, b.error, b.image_sha1) == (
        "verified", "13V", True, None, items()["burn"]["image_sha1"])


def test_reading_back_gives_the_example_as_posted(client, bench):
    upload(client, bench, img())
    post(client, bench, example("rom-ingest-request.json"))
    r = client.get("/api/v1/machines/widget-wars/dumps", headers=auth(bench))
    assert r.status_code == 200 and r.get_json() == example("machine-dumps-response.json")
    assert client.get("/api/v1/machines/nobody/dumps", headers=auth(bench)).status_code == 404


# --- the upload ----------------------------------------------------------------------------

def test_an_interrupted_upload_resumes_where_it_stopped(client, bench):
    data = os.urandom(9 * MIB)                                           # 8 MiB + 1 MiB
    sha = hashlib.sha256(data).hexdigest()
    b = client.post("/api/v1/files", json={"sha256": sha, "size": len(data)},
                    headers=auth(bench)).get_json()
    assert (b["state"], b["parts"], b["have"]) == ("open", 2, [])
    client.put(f"/api/v1/files/{sha}/parts/0", data=data[:rom_files.PART_SIZE],
               headers=auth(bench), content_type="application/octet-stream")
    b = client.post("/api/v1/files", json={"sha256": sha, "size": len(data)},
                    headers=auth(bench)).get_json()
    assert (b["state"], b["have"]) == ("open", [0])
    r = client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))
    assert r.status_code == 409 and "[1]" in r.get_json()["error"]
    client.put(f"/api/v1/files/{sha}/parts/1", data=data[rom_files.PART_SIZE:],
               headers=auth(bench), content_type="application/octet-stream")
    done = client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))
    assert done.status_code == 200
    assert done.get_json() == {"state": "stored", "sha256": sha, "size": len(data)}


def test_a_part_sent_twice_is_harmless_and_a_stored_file_ignores_parts(client, bench):
    data = img()
    sha = hashlib.sha256(data).hexdigest()
    client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench))
    for _ in range(2):
        r = client.put(f"/api/v1/files/{sha}/parts/0", data=data, headers=auth(bench),
                       content_type="application/octet-stream")
        assert r.get_json()["have"] == [0]
    assert client.post(f"/api/v1/files/{sha}/finish",
                       headers=auth(bench)).get_json()["state"] == "stored"
    late = client.put(f"/api/v1/files/{sha}/parts/0", data=b"junk", headers=auth(bench),
                      content_type="application/octet-stream")
    assert late.status_code == 200 and late.get_json()["state"] == "stored"
    assert client.post(f"/api/v1/files/{sha}/finish",
                       headers=auth(bench)).get_json()["state"] == "stored"


def test_a_join_that_is_not_the_announced_hash_is_dropped(client, bench, app):
    data = img()
    wrong = hashlib.sha256(b"something else").hexdigest()               # announced, never sent
    client.post("/api/v1/files", json={"sha256": wrong, "size": len(data)}, headers=auth(bench))
    client.put(f"/api/v1/files/{wrong}/parts/0", data=data, headers=auth(bench),
               content_type="application/octet-stream")
    r = client.post(f"/api/v1/files/{wrong}/finish", headers=auth(bench))
    assert r.status_code == 422 and "send the file again" in r.get_json()["error"]
    assert count("rom_file") == 0
    assert not (bench["dir"] / ".incoming" / wrong).exists()
    assert not rom_files.path_for(wrong).exists()
    again = client.post("/api/v1/files", json={"sha256": wrong, "size": len(data)},
                        headers=auth(bench))
    assert again.get_json()["have"] == []                               # begun afresh


def _put(client, bench, sha, n, chunk, content_type="application/octet-stream"):
    return client.put(f"/api/v1/files/{sha}/parts/{n}", data=chunk, headers=auth(bench),
                      content_type=content_type)


@pytest.mark.parametrize("what,call,code", [
    ("a part of a file never begun", lambda c, b, s: _put(c, b, s, 0, b"x" * 32), 404),
    ("finish of a file never begun",
     lambda c, b, s: c.post(f"/api/v1/files/{s}/finish", headers=auth(b)), 404),
    ("a size of 0",
     lambda c, b, s: c.post("/api/v1/files", json={"sha256": s, "size": 0}, headers=auth(b)), 400),
    ("a size that isn't a number",
     lambda c, b, s: c.post("/api/v1/files", json={"sha256": s, "size": "32"},
                            headers=auth(b)), 400),
    ("a size of true",
     lambda c, b, s: c.post("/api/v1/files", json={"sha256": s, "size": True},
                            headers=auth(b)), 400),
    ("a file over 32 MiB",
     lambda c, b, s: c.post("/api/v1/files", json={"sha256": s, "size": 32 * MIB + 1},
                            headers=auth(b)), 413),
    ("a hash in capitals",
     lambda c, b, s: c.post("/api/v1/files", json={"sha256": s.upper(), "size": 32},
                            headers=auth(b)), 400),
    ("a hash that is too short",
     lambda c, b, s: c.post("/api/v1/files", json={"sha256": s[:63], "size": 32},
                            headers=auth(b)), 400),
    ("no body", lambda c, b, s: c.post("/api/v1/files", headers=auth(b)), 400),
    ("a read of a file no record names",
     lambda c, b, s: c.get(f"/api/v1/files/{s}", headers=auth(b)), 404),
])
def test_files_calls_refuse(client, bench, what, call, code):
    assert call(client, bench, hashlib.sha256(b"nothing sent").hexdigest()).status_code == code, \
        what


def test_the_part_rules(client, bench):
    data = os.urandom(9 * MIB)
    sha = hashlib.sha256(data).hexdigest()
    client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench))
    first = data[:rom_files.PART_SIZE]
    assert _put(client, bench, sha, 0, first[:-1]).status_code == 400          # a short first part
    assert _put(client, bench, sha, 1, data[rom_files.PART_SIZE:] + b"x").status_code == 400
    assert _put(client, bench, sha, 2, b"x").status_code == 400                # outside 0..1
    assert _put(client, bench, sha, 2, data[rom_files.PART_SIZE:]).status_code == 400   # right length, no part 2
    assert _put(client, bench, sha, -1, b"x").status_code == 404
    assert _put(client, bench, sha, 0, first, "application/json").status_code == 415
    assert _put(client, bench, sha, 0, b"x" * (rom_files.PART_SIZE + 1)).status_code == 413
    assert _put(client, bench, sha, 0, first).status_code == 200


def test_stale_half_sent_uploads_are_swept(client, bench):
    stale = bench["dir"] / ".incoming" / ("ab" * 32)
    stale.mkdir(parents=True)
    old = time.time() - 8 * 24 * 3600
    os.utime(stale, (old, old))
    client.post("/api/v1/files", json={"sha256": hashlib.sha256(b"y").hexdigest(), "size": 1},
                headers=auth(bench))
    assert not stale.exists()


def test_a_device_reads_back_only_files_a_record_names(client, bench):
    data = img()
    sha = example("rom-image.json")["sha256"]
    upload(client, bench, data)
    assert client.get(f"/api/v1/files/{sha}", headers=auth(bench)).status_code == 404   # unnamed
    post(client, bench, example("rom-ingest-request.json"))
    r = client.get(f"/api/v1/files/{sha}", headers=auth(bench))
    assert r.status_code == 200 and r.get_data() == data
    assert r.headers["Cache-Control"] == "private, no-store"
    part = client.get(f"/api/v1/files/{sha}", headers={**auth(bench), "Range": "bytes=4-7"})
    assert part.status_code == 206 and part.get_data() == data[4:8]
    assert client.get(f"/api/v1/files/{sha}").status_code == 401


# --- the items: every refusal --------------------------------------------------------------

@pytest.fixture()
def uploaded(client, bench):
    upload(client, bench, img())
    return bench


def test_a_dump_is_rejected_for(client, uploaded):
    d = items()["rom_dump"]
    refused(client, uploaded, {**d, "machine": "nobody"}, "no machine with slug")
    refused(client, uploaded, sealed({**d, "label": ""}), "label is required")
    refused(client, uploaded, sealed({**d, "label": "L" * 41}), "label is longer than 40")
    refused(client, uploaded, {**d, "uid": "0" * 32}, "uid doesn't follow")
    refused(client, uploaded, {**d, "uid": "ABC"}, "32 lowercase hex")
    refused(client, uploaded, sealed({**d, "sha1": "0" * 40}), "sha1 isn't the stored file's")
    refused(client, uploaded, {**d, "crc32": "00000000"}, "crc32 isn't the stored file's")
    refused(client, uploaded, {**d, "size": 31}, "size 31 isn't the stored file's")
    refused(client, uploaded, {**d, "size": 0}, "size must be 1 or more")
    refused(client, uploaded, {**d, "sha256": "f" * 64}, "send the file first")
    refused(client, uploaded, {**d, "identical": False}, "identical")
    refused(client, uploaded, {**d, "reads": 1}, "reads must be 2 or more")
    refused(client, uploaded, {**d, "blank": "no"}, "blank must be true or false")
    refused(client, uploaded, {**d, "created": None}, "created is required")
    refused(client, uploaded, {**d, "clock": {"source": "sundial"}}, "clock.source")
    refused(client, uploaded, {**d, "romident": {"match": "yes"}}, "romident must be")
    refused(client, uploaded, {**d, "romident": {"match": True, "matches": [{"set": "x"}]}},
            "romident rom is required")
    refused(client, uploaded, {**d, "crc32": "ABCDEF01"}, "crc32 must be 8 lowercase hex")
    assert count("rom_dump") == 0


def test_a_dump_without_its_file_is_rejected_and_then_accepted(client, bench):
    d = items()["rom_dump"]
    refused(client, bench, d, "send the file first")
    upload(client, bench, img())
    assert one(client, bench, d)["status"] == "created"


def test_a_dump_may_say_mame_did_not_run_or_found_nothing(client, uploaded):
    from app.models import RomDump

    d = items()["rom_dump"]
    assert one(client, uploaded, {**d, "romident": None})["status"] == "created"
    assert RomDump.query.one().romident is None
    other = sealed({**d, "label": "U11", "romident": {"match": False}})
    assert one(client, uploaded, other)["status"] == "created"
    assert RomDump.query.filter_by(label="U11").one().romident == {"match": False}


def test_a_burn_is_rejected_for(client, uploaded):
    b = items()["burn"]
    refused(client, uploaded, {**b, "machine": "nobody"}, "no machine with slug")
    refused(client, uploaded, {**b, "uid": "0" * 32}, "uid doesn't follow")
    refused(client, uploaded, {**b, "created": b["created"] + 0.001}, "uid doesn't follow")
    refused(client, uploaded, sealed({**b, "image_sha1": "0" * 40}), "sha1 isn't the stored file's")
    refused(client, uploaded, {**b, "image": "e" * 64}, "send the file first")
    refused(client, uploaded, {**b, "size": 31}, "isn't the stored file's")
    refused(client, uploaded, {**b, "state": "done"}, "state must be one of")
    refused(client, uploaded, {**b, "blank_ok": "yes"}, "blank_ok")
    refused(client, uploaded, {**b, "error": "x" * 2001}, "longer than 2000")
    refused(client, uploaded, {**b, "vpp": "x" * 21}, "vpp is longer")
    refused(client, uploaded, {**b, "created": None}, "created is required")
    assert count("rom_burn") == 0


def test_a_stopped_burn_is_kept_with_its_reason(client, uploaded):
    from app.models import RomBurn

    b = items()["burn"]
    stopped = sealed({**b, "state": "stopped", "created": b["created"] + 60, "blank_ok": None,
                      "vpp": None, "error": "blank check failed: the chip isn't blank"})
    assert one(client, uploaded, stopped)["status"] == "created"
    row = RomBurn.query.one()
    assert (row.state, row.error, row.blank_ok, row.vpp) == (
        "stopped", "blank check failed: the chip isn't blank", None, None)


def test_a_uid_is_for_the_device_that_made_it(client, uploaded):
    from app.extensions import db
    from app.models import Device

    other = Device(name="other-bench")
    other.public_id = "b1b2b3b4b5b6"
    token = other.issue_token()
    db.session.add(other)
    db.session.commit()
    post(client, uploaded, example("rom-ingest-request.json"))
    d = items()["rom_dump"]
    headers = {"Authorization": f"Bearer {token}"}
    own = client.post("/api/v1/ingest", json={"contract": "v1", "device": "b1b2b3b4b5b6",
                                              "items": [sealed(d, "b1b2b3b4b5b6")]},
                      headers=headers)
    assert own.get_json()["results"][0]["status"] == "created"        # its own record, same file
    assert (count("rom_dump"), count("rom_file")) == (2, 1)
    steal = client.post("/api/v1/ingest", json={"contract": "v1", "device": "b1b2b3b4b5b6",
                                                "items": [d]}, headers=headers)
    reason = steal.get_json()["results"][0]
    assert reason["status"] == "rejected" and "uid doesn't follow" in reason["reason"]


def test_a_rejected_item_leaves_the_rest_of_the_request_alone(client, uploaded):
    good, bad = items()["rom_dump"], items()["burn"]
    r = post(client, uploaded, {"contract": "v1", "device": PUBLIC_ID,
                                "items": [{**bad, "state": "x"}, good]})
    assert [x["status"] for x in r.get_json()["results"]] == ["rejected", "created"]
    assert (count("rom_dump"), count("rom_burn")) == (1, 0)


# --- the device allow-list -----------------------------------------------------------------

def limit_to(*slugs):
    from app.extensions import db
    from app.models import Device, Game

    dev = Device.query.filter_by(name="test-bench").one()
    dev.all_machines = False
    dev.machines = Game.query.filter(Game.barcode.in_(slugs)).all()
    db.session.commit()


def test_a_device_limited_to_other_machines_cannot_send_read_or_list(client, uploaded):
    post(client, uploaded, example("rom-ingest-request.json"))
    limit_to("pin-sprocket")
    r = one(client, uploaded, sealed({**items()["rom_dump"], "label": "U12"}))
    assert r["status"] == "rejected" and "is not on this device's list" in r["reason"]
    r = one(client, uploaded, items()["burn"])
    assert r["status"] == "rejected" and "is not on this device's list" in r["reason"]
    sha = example("rom-image.json")["sha256"]
    assert client.get(f"/api/v1/files/{sha}", headers=auth(uploaded)).status_code == 404
    assert client.get("/api/v1/machines/widget-wars/dumps",
                      headers=auth(uploaded)).status_code == 404
    limit_to("widget-wars")
    assert client.get(f"/api/v1/files/{sha}", headers=auth(uploaded)).status_code == 200
    assert client.get("/api/v1/machines/widget-wars/dumps",
                      headers=auth(uploaded)).status_code == 200


# --- the order link ------------------------------------------------------------------------

def make_order(slug="widget-wars"):
    from app.extensions import db
    from app.models import Game, MaintenanceRecord

    game = Game.query.filter_by(barcode=slug).one()
    order = MaintenanceRecord(game_id=game.id, issue_description="Rail sags under load",
                              status="Open")
    db.session.add(order)
    db.session.commit()
    return order.id


def test_a_dump_for_an_order_reads_back_with_it_and_shows_on_the_order(client, uploaded, app):
    from app.models import RomDump

    order = make_order()
    assert one(client, uploaded, {**items()["rom_dump"], "order": order})["status"] == "created"
    assert RomDump.query.one().maintenance_record_id == order
    assert one(client, uploaded, {**items()["burn"], "order": order})["status"] == "created"
    back = client.get("/api/v1/machines/widget-wars/dumps", headers=auth(uploaded)).get_json()
    assert back["dumps"][0]["order"] == order and back["burns"][0]["order"] == order
    page = signed_in(app, client, "operator").get(f"/maintenance_detail/{order}")
    html = page.get_data(as_text=True)
    assert "ROM dumps and burns" in html and "Dump U10" in html and "Burn · " in html


def test_an_order_that_is_not_the_machines_is_refused(client, uploaded):
    other = make_order("pin-sprocket")
    refused(client, uploaded, {**items()["rom_dump"], "order": other}, "different machine")
    refused(client, uploaded, {**items()["burn"], "order": other}, "different machine")
    refused(client, uploaded, {**items()["rom_dump"], "order": 999999}, "no order with id")
    refused(client, uploaded, {**items()["rom_dump"], "order": "7"}, "integer id")
    refused(client, uploaded, {**items()["rom_dump"], "order": True}, "integer id")
    assert count("rom_dump") == 0


# --- what people see, and may download -----------------------------------------------------

def signed_in(app, client, role):
    from app.extensions import db
    from app.models import User

    user = User(username=f"t-{role}", role=role, must_change_password=False)
    user.set_password("not-a-real-password")
    db.session.add(user)
    db.session.commit()
    client.post("/login", data={"username": f"t-{role}", "password": "not-a-real-password"})
    return client


def test_the_machine_page_lists_them_and_the_image_downloads_for_operators(client, uploaded, app):
    from app.models import Game, RomBurn, RomDump

    post(client, uploaded, example("rom-ingest-request.json"))
    game_id = Game.query.filter_by(barcode="widget-wars").one().id
    dump_id, burn_id = RomDump.query.one().id, RomBurn.query.one().id
    signed_in(app, client, "operator")
    page = client.get(f"/game/{game_id}").get_data(as_text=True)
    assert "ROM dumps and burns" in page and "Dump U10" in page and "MAME match" in page
    assert "widgetw/ww-u10.bin" in page and "verified" in page and "13V" in page
    assert f"/roms/dump/{dump_id}/image" in page and f"/roms/burn/{burn_id}/image" in page
    r = client.get(f"/roms/dump/{dump_id}/image")
    assert r.status_code == 200 and r.get_data() == img()
    assert "attachment" in r.headers["Content-Disposition"]
    assert "widget-wars_U10_" in r.headers["Content-Disposition"]
    assert r.headers["Cache-Control"] == "private, no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(f"/roms/burn/{burn_id}/image").get_data() == img()
    assert client.get("/roms/dump/999999/image").status_code == 404


def test_a_readonly_user_neither_sees_nor_downloads(client, uploaded, app):
    from app.models import Game, RomBurn, RomDump

    post(client, uploaded, example("rom-ingest-request.json"))
    game_id = Game.query.filter_by(barcode="widget-wars").one().id
    dump_id, burn_id = RomDump.query.one().id, RomBurn.query.one().id
    signed_in(app, client, "readonly")
    assert "ROM dumps and burns" not in client.get(f"/game/{game_id}").get_data(as_text=True)
    for url in (f"/roms/dump/{dump_id}/image", f"/roms/burn/{burn_id}/image"):
        r = client.get(url)
        assert r.status_code == 302 and img() not in r.get_data(), url


def test_nobody_without_a_login_gets_an_image(client, uploaded):
    from app.models import RomDump

    post(client, uploaded, example("rom-ingest-request.json"))
    dump_id = RomDump.query.one().id
    for headers in ({}, auth(uploaded)):                      # a device's token is not a login
        r = client.get(f"/roms/dump/{dump_id}/image", headers=headers)
        assert r.status_code in (302, 401) and img() not in r.get_data()


def test_the_images_have_no_url_of_their_own(client, uploaded, app):
    sha = example("rom-image.json")["sha256"]
    path = rom_files.path_for(sha)
    assert path.is_file() and path.read_bytes() == img() and path.suffix == ".rom"
    upload_root = os.path.realpath(app.config["UPLOAD_FOLDER"])
    static = os.path.realpath(app.static_folder)
    real = os.path.realpath(path)
    assert not real.startswith(static + os.sep)
    # the default home is a folder under uploads/, which the one route serving from there
    # (a bare name) can't reach
    default = os.path.join(upload_root, "rom_files")
    assert os.path.relpath(default, upload_root) == "rom_files"
    assert client.get(f"/static/{sha}.rom").status_code == 404
    signed_in(app, client, "admin")
    for url in (f"/parts/photos/{sha}.rom", "/parts/photos/rom_files", f"/parts/photos/part_/../{sha}"):
        assert client.get(url).status_code in (404, 308)


def test_the_example_image_is_what_its_hashes_say():
    i = example("rom-image.json")
    b = bytes.fromhex(i["hex"])
    assert hashlib.sha1(b).hexdigest() == i["sha1"] and zlib.crc32(b) == int(i["crc32"], 16)


# --- the migration ------------------------------------------------------------------------

def test_the_migration_round_trips(app):
    import sqlalchemy as sa
    from flask_migrate import downgrade, stamp, upgrade

    from app.extensions import db

    def migrate(step, revision):
        db.session.commit()
        step(directory=os.path.join(ROOT, "migrations"), revision=revision)

    tables = ("rom_file", "rom_dump", "rom_burn")
    stamp(directory=os.path.join(ROOT, "migrations"), revision="e5f6a7b8c9d0")
    migrate(downgrade, "c4d5e6f7a8b9")
    assert not any(sa.inspect(db.engine).has_table(t) for t in tables)
    migrate(upgrade, "e5f6a7b8c9d0")
    migrate(upgrade, "e5f6a7b8c9d0")                         # guarded: a re-run is harmless
    assert all(sa.inspect(db.engine).has_table(t) for t in tables)
