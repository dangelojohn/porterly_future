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

from porterly import agents, policy, projections
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
    # A guest is reached on their own phone (SMS in production): disclose the options so the
    # parcel is awaiting the guest's consent and the guest link goes live.
    if projections.parcel_state(STORE, pid).get("kind") == "guest":
        CORE.disclose(pid, "api", base + ":disclose", ["pickup", "room"])
    return projections.parcel_state(STORE, pid)


def guest_view(pid):
    """What the guest sees from their texted link — first name only, priced options from policy."""
    st = projections.parcel_state(STORE, pid)
    if not st.get("parcel_id"):
        return {"found": False}
    first = (st.get("recipient") or "").split(" ")[0] if st.get("recipient") else "you"
    options = []
    for key, label in (("pickup", "Pick it up at the front desk"), ("room", "Bring it to my room")):
        amt, _ = policy.price_inbound(key)
        options.append({"key": key, "label": label, "price": amt})
    return {"found": True, "parcel_id": pid, "first_name": first, "state": st["state"],
            "awaiting": st["state"] == "awaiting_consent", "options": options,
            "charges": st.get("charges", [])}


def guest_consent(pid, choice):
    """The guest's reply -> the core records consent (policy price) and hands the parcel over.
    Only the deterministic core moves money/state; the page just relays the choice."""
    if choice not in ("pickup", "room"):
        raise Rejected("please choose pick-up or room delivery")
    if projections.parcel_state(STORE, pid)["state"] != "awaiting_consent":
        raise Rejected("this package has already been handled")
    base = _op()
    if choice == "room":
        CORE.consent(pid, "guest", base + ":consent", "room", "guest")   # $8 at the policy price
    CORE.release(pid, "guest", base + ":release", "guest-app", choice=choice, payer="guest")
    CORE.close(pid, "system", base + ":close")
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
 .apps{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin:0 0 1.8rem}
 @media(max-width:34rem){.apps{grid-template-columns:1fr}}
 .app{display:block;padding:1rem 1.15rem;background:#fffdf7;border:1px solid #d8d0be;border-left:3px solid #9a6a2b;border-radius:.6rem;text-decoration:none;color:inherit}
 .app b{display:block;font:600 1.1rem/1 ui-serif,Georgia,serif;color:#9a6a2b}.app span{display:block;color:#8a7f6a;font-size:.84rem;margin-top:.25rem}
 h2{font:600 .78rem/1 ui-serif,Georgia,serif;color:#8a7f6a;text-transform:uppercase;letter-spacing:.08em;margin:0 0 .5rem}
 @media(prefers-color-scheme:dark){.app{background:#201d16;border-color:#3a3428}.app b{color:#c79a5a}.app span,.note,.sub,h2{color:#9a9083}a{color:#c79a5a}}
</style>
<h1>Porterly&nbsp;Future</h1>
<p class=sub>Reference implementation of the AI-native architecture &mdash; not the live system.</p>
<div class=apps>
 <a class=app href="/dock"><b>Dock &rarr;</b><span>Inbound capture. Receive a parcel; watch the core corroborate &amp; shelve it.</span></a>
 <a class=app href="/console"><b>Console &rarr;</b><span>Manager surface. Parcels, the storage twin &amp; the settled statement.</span></a>
</div>
<h2>Raw API</h2>
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

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PAGES = {"/dock": "dock.html", "/console": "console.html"}


def _page(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


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
            if u.path in PAGES:
                return self._send_html(200, _page(PAGES[u.path]))
            if u.path.startswith("/g/"):
                return self._send_html(200, _page("guest.html"))
            if u.path == "/guest":
                return self._send(200, guest_view(q.get("id", [""])[0]))
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
        if u.path == "/guest/consent":
            try:
                return self._send(200, guest_consent(body.get("id", ""), body.get("choice", "")))
            except Rejected as e:
                return self._send(422, {"rejected": str(e)})
            except (InvalidTransition, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except Exception:
                return self._send(500, {"error": "internal error"})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print("Porterly reference service on http://%s:%d  (db=%s, Ctrl-C to stop)" % (HOST, PORT, DB))
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
