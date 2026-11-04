"""A stand-in for GATBOX's archive that speaks the vendored read-side contract (contract/library-v1/).

It starts from the contract's own examples (every record of ``changes-response-all.json``, the manual the
example document names) and answers the two calls an app makes, the way the contract says: the change feed with
``models=`` (the cursor moves past what the filter skipped, a page can be empty with ``more`` true), and a
manual by ``Range``. tests/test_library_pull.py has a test that asks it the contract's own requests and gets the
contract's own answers, so the stub can't drift from the examples without a test saying so. Tests then bend it:
a changed record, a tombstone, a retired game, a restore from an older copy, a refused token, a file cut off half
way. Every game and byte is invented (they are the contract's).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "contract", "library-v1", "examples")
TOKEN = "gbx_a1b2c3d4e5f6.not-a-real-secret"        # a reader token's shape; not a real one


def example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


with open(os.path.join(EXAMPLES, "manual.pdf.b64"), "rb") as _f:
    MANUAL = base64.b64decode(_f.read())  # the contract ships it as base64: a repository refuses a .pdf
MANUAL_SHA = hashlib.sha256(MANUAL).hexdigest()


def model_of(rec):
    return rec["id"] if rec["kind"] == "game" else (rec["data"] or {}).get("model_key")


def canonical_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class Archive:
    """The archive's state: rows (seq, record), a head, files by hash, and switches the tests flip."""

    def __init__(self):
        recs = copy.deepcopy(example("changes-response-all.json")["records"])
        seqs = list(range(1, len(recs))) + [11]                 # the example's tombstone is the one at 11
        self.rows = list(zip(seqs, recs, strict=True))
        self.head = 11
        self.files = {MANUAL_SHA: MANUAL}
        self.token = None                                        # any non-empty bearer token, until a test sets one
        self.redirect = None                                     # answer every request with a 302 to this address
        self.refuse = False                                      # answer 403 to everything
        self.cut = {}                                            # sha -> bytes to send of a part before dropping the connection (once)
        self.lie = {}                                            # sha -> bytes to send instead of the real ones
        self.requests = []                                       # (path, headers)

    def next_seq(self):
        self.head += 1
        return self.head

    def put(self, rec):
        """Add or replace a record (a changed one moves to the end of the feed, like the real archive's)."""
        self.rows = [(s, r) for s, r in self.rows if (r["kind"], r["id"]) != (rec["kind"], rec["id"])]
        rec = copy.deepcopy(rec)
        rec["hash"] = canonical_hash(rec["data"])
        self.rows.append((self.next_seq(), rec))
        return rec

    def feed(self, since, limit, models):
        raw = [(s, r) for s, r in sorted(self.rows, key=lambda x: x[0]) if s > since][:limit + 1]
        more = len(raw) > limit
        raw = raw[:limit]
        recs = [r for _, r in raw if models is None or model_of(r) in models]
        return {"records": recs, "cursor": raw[-1][0] if raw else since, "more": more, "head": self.head}


class Handler(BaseHTTPRequestHandler):
    archive: Archive = None

    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        a = self.archive
        url = urlsplit(self.path)
        a.requests.append((self.path, dict(self.headers)))
        if a.redirect:
            self.send_response(302)
            self.send_header("Location", a.redirect + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        auth = self.headers.get("Authorization", "")
        if a.refuse or not auth.startswith("Bearer ") or (a.token and auth[7:] != a.token):
            return self.reply(403 if a.refuse else 401, {"error": "a valid token is required"})
        if url.path == "/api/v1/library/changes":
            q = parse_qs(url.query)
            models = q.get("models", [""])[0]
            return self.reply(200, a.feed(int(q.get("since", ["0"])[0]), min(int(q.get("limit", ["500"])[0]), 500),
                                          set(models.split(",")) if models else None))
        if url.path.startswith("/api/v1/files/"):
            sha = url.path.rsplit("/", 1)[1]
            body = a.lie.get(sha) or a.files.get(sha)
            if body is None:
                return self.reply(404, {"error": "no such file"})
            rng = self.headers.get("Range")
            if not rng:
                return self.reply(200, body, "application/pdf")
            first, _, last = rng.split("=")[1].partition("-")
            first = int(first)
            if first >= len(body):
                return self.reply(416, {"error": "that range is outside the file"})
            end = min(int(last) if last else len(body) - 1, len(body) - 1)
            chunk = body[first:end + 1]
            if sha in a.cut:
                n = a.cut.pop(sha)
                self.send_response(206)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Content-Range", f"bytes {first}-{end}/{len(body)}")
                self.end_headers()
                self.wfile.write(chunk[:n])
                self.wfile.flush()
                self.close_connection = True
                return
            return self.reply(206, chunk, "application/pdf", [("Content-Range", f"bytes {first}-{end}/{len(body)}"), ("Accept-Ranges", "bytes"),
                                                              ("ETag", f'"{sha}"')])
        self.reply(404, {"error": "not found"})


class Server:
    def __init__(self):
        self.archive = Archive()
        handler = type("H", (Handler,), {"archive": self.archive})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
