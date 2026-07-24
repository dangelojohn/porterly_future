#!/usr/bin/env python3
"""A tiny read/write HTTP surface over the same spine (stdlib only).

    python3 serve.py           # then browse http://127.0.0.1:8787/health

This is NOT the production API (that's FastAPI + managed Postgres). It exists to
show the ledger/core driving real HTTP endpoints with no framework:

    GET  /health
    GET  /parcels
    GET  /parcel?id=PR-...
    GET  /find?q=Sarah
    GET  /storage
    GET  /reconcile
    POST /inbound   {"recipient_hint","carrier","tracking","weight_lb","location"}
"""
import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from porterly import agents, projections
from porterly.core import Core, Rejected
from porterly.fsm import InvalidTransition
from porterly.ledger import EventStore

# Config from the environment (12-factor). Defaults keep the local demo unchanged:
# localhost, in-memory. A deployment can override to bind the LAN and persist to a file.
HOST = os.environ.get("PORTERLY_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORTERLY_PORT", "8787"))
DB = os.environ.get("PORTERLY_DB", ":memory:")   # a file path persists across restarts

STORE = EventStore(DB)
CORE = Core(STORE)


MAX_BODY = 64 * 1024   # cap request bodies — no unbounded read


def _op():
    return uuid.uuid4().hex


def inbound(recipient_hint, carrier="UPS", tracking=None, weight_lb=5.0,
            location="TSC · MR Cage · C1", idem=None):
    """Arrive -> capture -> propose -> corroborate -> store, returning the parcel. When an
    Idempotency-Key is supplied, every sub-event op derives from it, so a retried POST is a
    no-op (same parcel) instead of creating a duplicate."""
    if not isinstance(recipient_hint, str) or not recipient_hint.strip():
        raise ValueError("recipient_hint must be a non-empty string")
    base = idem or _op()
    tag = "".join(ch for ch in base if ch.isalnum())[:8].upper() or "PKG"
    pid = CORE.arrive("api", base + ":arrive", {"recipient_hint": recipient_hint})
    CORE.capture(pid, "api", base + ":capture", {"carrier": carrier, "tracking": tracking or ("1Z" + tag),
                                                 "weight_lb": weight_lb, "dims": "18x12x10"})
    CORE.condition_photo(pid, "api", base + ":cond", "s3://evidence/%s.jpg" % pid)
    CORE.apply_intent(agents.propose_identity(pid, {"recipient_hint": recipient_hint}), "api", base + ":id")
    CORE.store_at(pid, "api", base + ":store", location, "s3://shelf/%s.jpg" % pid)
    return projections.parcel_state(STORE, pid)


# seed a couple so the endpoints show data immediately — only into an EMPTY store, so a
# persisted (file-backed) deployment isn't re-seeded on every restart. Disable with PORTERLY_SEED=0.
if os.environ.get("PORTERLY_SEED", "1") == "1" and STORE.count() == 0:
    for _hint, _loc in [("Sarah Chen", "TSC · MR Cage · C1"), ("Acme", "TSC · BC Cage · A2")]:
        try:
            inbound(_hint, location=_loc)
        except (Rejected, InvalidTransition):
            pass


INDEX_HTML = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Porterly Future — reference service</title>
<style>
 :root{color-scheme:light dark}
 body{max-width:44rem;margin:3rem auto;padding:0 1.25rem;
   font:16px/1.6 ui-serif,Georgia,"Times New Roman",serif;color:#1c1a17;background:#f6f3ec}
 @media(prefers-color-scheme:dark){body{color:#ece7dd;background:#17150f}}
 h1{font-size:1.9rem;margin:.2rem 0 .1rem;letter-spacing:.01em}
 .sub{color:#8a7f6a;margin:0 0 1.6rem;font-style:italic}
 a{color:#9a6a2b;text-decoration:none} a:hover{text-decoration:underline}
 code{font-family:ui-monospace,Menlo,monospace;font-size:.92em}
 ul{list-style:none;padding:0} li{padding:.35rem 0;border-bottom:1px solid #d8d0be55}
 .m{display:inline-block;min-width:3.3rem;font-weight:600;color:#9a6a2b}
 .note{margin-top:1.6rem;font-size:.86rem;color:#8a7f6a}
</style>
<h1>Porterly&nbsp;Future</h1>
<p class=sub>Reference implementation of the AI-native architecture &mdash; not the live system.</p>
<ul>
 <li><span class=m>GET</span> <a href="/health">/health</a> &mdash; service + event count</li>
 <li><span class=m>GET</span> <a href="/reconcile">/reconcile</a> &mdash; the settled statement</li>
 <li><span class=m>GET</span> <a href="/parcels">/parcels</a> &mdash; every parcel's state</li>
 <li><span class=m>GET</span> <a href="/storage">/storage</a> &mdash; the shelf digital twin</li>
 <li><span class=m>GET</span> <a href="/find?q=Sarah">/find?q=&hellip;</a> &mdash; find a package</li>
 <li><span class=m>GET</span> <code>/parcel?id=PR-&hellip;</code> &mdash; one parcel + custody chain</li>
 <li><span class=m>POST</span> <code>/inbound {"recipient_hint":"&hellip;"}</code> &mdash; receive a parcel (optional <code>Idempotency-Key</code> header)</li>
</ul>
<p class=note>Perception proposes, the deterministic core corroborates and acts. Stdlib only; append-only ledger.</p>
</html>"""


class H(BaseHTTPRequestHandler):
    timeout = 15   # socket timeout — a stalled/slow-loris connection can't hold a thread forever

    def _send(self, code, obj):
        body = json.dumps(obj, indent=2, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", ""):
                return self._send_html(200, INDEX_HTML)
            if u.path == "/favicon.ico":
                return self._send(404, {"error": "no favicon"})
            if u.path == "/health":
                return self._send(200, {"ok": True, "events": STORE.count(),
                                        "states": [p["state"] for p in projections.all_parcels(STORE)]})
            if u.path == "/parcels":
                return self._send(200, projections.all_parcels(STORE))
            if u.path == "/parcel":
                return self._send(200, projections.parcel_state(STORE, q.get("id", [""])[0]))
            if u.path == "/find":
                return self._send(200, projections.find(STORE, q.get("q", [""])[0]))
            if u.path == "/storage":
                return self._send(200, projections.storage_map(STORE))
            if u.path == "/reconcile":
                return self._send(200, projections.reconcile(STORE))
            return self._send(404, {"error": "not found"})
        except Exception:  # pragma: no cover
            return self._send(500, {"error": "internal error"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return self._send(400, {"error": "invalid Content-Length"})
        if n < 0 or n > MAX_BODY:
            return self._send(413, {"error": "request body too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "body must be a JSON object"})
        if u.path == "/inbound":
            idem = self.headers.get("Idempotency-Key")
            try:
                return self._send(201, inbound(idem=idem, **body))
            except Rejected as e:
                return self._send(422, {"rejected": str(e)})     # the core refused it
            except (InvalidTransition, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except TypeError:
                return self._send(400, {"error": "unexpected or missing request field"})
            except Exception:
                return self._send(500, {"error": "internal error"})   # never leak internals
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print("Porterly reference service on http://%s:%d  (db=%s, Ctrl-C to stop)" % (HOST, PORT, DB))
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
