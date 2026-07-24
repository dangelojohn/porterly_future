#!/usr/bin/env python3
"""Invariant tests — the properties that make the system safe.

    python3 tests.py     (exit 0 = all pass)

These are the "invalid states made impossible" the whole design is judged by.
"""
import sys
import threading
import uuid

from porterly import agents, contacts, fsm, policy, projections, validators
from porterly.core import Core, Rejected
from porterly.ledger import EventStore


def op():
    return uuid.uuid4().hex


PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s" % name)


def expect_raise(name, exc, fn):
    global PASS, FAIL
    try:
        fn(); FAIL += 1; print("  FAIL %s (no raise)" % name)
    except exc:
        PASS += 1; print("  ok   %s" % name)


def fresh():
    s = EventStore()
    return s, Core(s)


# --- idempotency: a retried command writes exactly once ---
s, c = fresh()
p = c.arrive("t", op(), {"recipient_hint": "Sarah Chen"})
o = op()
_, m1 = c.capture(p, "t", o, {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
n = s.count()
_, m2 = c.capture(p, "t", o, {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
check("idempotent append (created True then False)", m1 and not m2)
check("idempotent append writes no duplicate", s.count() == n)

# --- FSM guard: illegal transition raises before any write ---
s, c = fresh()
p = c.arrive("t", op(), {"recipient_hint": "Sarah Chen"})
c.capture(p, "t", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
before = s.count()
expect_raise("ship-before-store blocked", fsm.InvalidTransition,
             lambda: c.ship(p, "t", op(), "UPS", "x", 10, "guest", "d"))
check("blocked transition wrote nothing", s.count() == before)

# --- propose->validate: low confidence & human gate are refused ---
s, c = fresh()
p = c.arrive("t", op(), {"recipient_hint": "chen"})
c.capture(p, "t", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
expect_raise("ambiguous match rejected", Rejected,
             lambda: c.apply_intent(agents.propose_identity(p, {"recipient_hint": "chen"}), "t", op()))
expect_raise("hazmat human-gate rejected", Rejected,
             lambda: c.apply_intent(agents.propose_identity(p, {"recipient_hint": "Sarah Chen", "flags": ["hazmat"]}), "t", op()))
# the gate must survive casing / hyphen / space variants (an LLM won't emit canonical strings)
for _variant in ["Hazmat", "HAZMAT", "high-value", "HIGH VALUE", "address change"]:
    expect_raise("human-gate catches variant %r" % _variant, Rejected,
                 lambda v=_variant: c.apply_intent(
                     agents.propose_identity(p, {"recipient_hint": "Sarah Chen", "flags": [v]}), "t", op()))
check("gate set pinned (no silent removal)",
      validators.HUMAN_GATE == {"hazmat", "customs", "high_value", "address_change", "batch_gt_10"})


# --- H2: the core CORROBORATES identity against the directory (a proposal is data, not authority) ---
# a forged recipient_id that contradicts the arrival observation is refused
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
forged = agents.ProposedIntent(kind="identify", parcel_id=p, match_confidence=1.0,
                               fields={"recipient_id": "acme", "recipient": "Acme Robotics", "kind": "exhibitor"})
expect_raise("H2: forged recipient contradicting observation rejected", Rejected,
             lambda: c.apply_intent(forged, "attacker", op()))
check("H2: forged identity wrote nothing", "identified" not in [e["type"] for e in s.for_parcel(p)])

# a hallucinated confidence on an ambiguous observation is refused (the proposer's 1.0 is ignored)
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "chen"})   # ambiguous: Sarah + James
c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
liar = agents.ProposedIntent(kind="identify", parcel_id=p, match_confidence=1.0,
                             fields={"recipient_id": "sarah", "recipient": "Sarah Chen"})
expect_raise("H2: hallucinated confidence on ambiguous obs rejected", Rejected,
             lambda: c.apply_intent(liar, "attacker", op()))

# tampered fields (kind / master_account) are IGNORED — the core writes the directory's truth
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
tampered = agents.ProposedIntent(kind="identify", parcel_id=p, match_confidence=1.0,
                                 fields={"recipient_id": "sarah", "kind": "exhibitor",
                                         "master_account": "MA-EVIL", "company": "HAX"})
c.apply_intent(tampered, "proposer", op())
st = projections.parcel_state(s, p)
check("H2: kind comes from directory, not proposer", st["kind"] == "guest")
check("H2: forged master_account discarded", st["master_account"] is None)
check("H2: recipient comes from directory", st["recipient"] == "Sarah Chen")

# the core acts only on a typed proposal — a raw dict cannot drive it
expect_raise("H2: core acts only on a typed ProposedIntent", Rejected,
             lambda: c.apply_intent({"kind": "identify", "fields": {"recipient_id": "sarah"}}, "x", op()))


# --- H4: reusing an op for DIFFERENT data is a collision, not a silent stale return ---
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
myop = op()
c.capture(p, "Peter", myop, {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
_, again = c.capture(p, "Peter", myop, {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
check("H4: identical retry is still a no-op", again is False)
expect_raise("H4: op reused for different data rejected", Rejected,
             lambda: c.capture(p, "Peter", myop, {"carrier": "FedEx", "tracking": "9Z", "weight_lb": 9}))

# --- M1: pricing refuses unknown inputs instead of silently mis-pricing ---
expect_raise("M1: unknown outbound mode rejected", ValueError, lambda: policy.price_outbound("Freight", 100))
expect_raise("M1: unknown accessorial rejected", ValueError, lambda: policy.price_outbound("freight", 100, ("bogus",)))
expect_raise("M1: non-positive weight rejected", ValueError, lambda: policy.price_outbound("parcel", 0))
expect_raise("M1: None weight rejected", ValueError, lambda: policy.price_outbound("freight", None))
expect_raise("M1: unknown inbound choice rejected", ValueError, lambda: policy.price_inbound("Room"))
check("M1: auto_cover rejects negative amounts", policy.auto_cover(-5) is False)

# --- M2: the 'billed' money event is guarded (only from released / shipped) ---
expect_raise("M2: billed blocked from an arbitrary state", fsm.InvalidTransition,
             lambda: fsm.next_state("stored", "billed"))
check("M2: billed allowed after released", fsm.next_state("released", "billed") == "released")
check("M2: billed allowed after shipped", fsm.next_state("shipped", "billed") == "shipped")

# --- M3: received-but-unshelved parcels are visible in the storage twin (no blind spot) ---
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": 5})
twin = projections.storage_map(s)
check("M3: captured parcel appears in the intake bucket", any(p in ids for ids in twin.values()))
check("M3: intake bucket is labelled", any("intake" in loc for loc in twin))

# --- semantic validator: implausible weight refused ---
s, c = fresh()
p = c.arrive("t", op(), {"recipient_hint": "Sarah Chen"})
expect_raise("implausible weight rejected", Rejected,
             lambda: c.capture(p, "t", op(), {"weight_lb": 99999}))


# --- policy & consent ENFORCED in the core (the no-ambush guarantee) ---
def identified(hint="Sarah Chen", weight=6.0):
    s, c = fresh()
    p = c.arrive("Peter", op(), {"recipient_hint": hint})
    c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1Z", "weight_lb": weight})
    c.apply_intent(agents.propose_identity(p, {"recipient_hint": hint}), "Peter", op())
    c.store_at(p, "Peter", op(), "TSC · MR Cage · C1")
    return s, c, p


# consent price is authoritative — computed by the core from policy, not passed by the caller
s, c, p = identified("Acme", weight=22.0)
c.disclose(p, "system", op(), ["ship"])
amt = c.consent(p, "acme", op(), "ship", "exhibitor")
check("consent price is policy-authoritative", amt == policy.price_outbound("parcel", 22.0))

# the free amenity ($0) needs no consent
s, c, p = identified("Sarah Chen")
c.disclose(p, "system", op(), ["pickup"])
c.release(p, "Desk", op(), "sig", choice="pickup", payer="guest")
check("free pickup needs no consent", projections.parcel_state(s, p)["state"] == "released")
check("free pickup billed $0", projections.parcel_state(s, p)["charges"][-1]["amount"] == 0.0)

# a PAID charge with no matching consent is refused — and leaves NO partial write
s, c, p = identified("Sarah Chen")
c.disclose(p, "system", op(), ["room"])
expect_raise("no-ambush: paid release without consent rejected", Rejected,
             lambda: c.release(p, "Desk", op(), "sig", choice="room", payer="guest"))
check("rejected charge left no partial write", projections.parcel_state(s, p)["state"] == "awaiting_consent")
check("rejected charge billed nothing", projections.parcel_state(s, p)["charges"] == [])

# a charge EXCEEDING consent is refused (consent to cheap freight $110, try to ship pricier parcel $212)
s, c, p = identified("Acme", weight=100.0)
c.disclose(p, "system", op(), ["freight"])
c.consent(p, "acme", op(), "freight", "exhibitor")
expect_raise("no-ambush: charge exceeding consent rejected", Rejected,
             lambda: c.ship(p, "Peter", op(), "LTL", "X", "exhibitor", "s3://d", choice="ship"))
check("over-consent charge left parcel un-shipped", projections.parcel_state(s, p)["state"] == "consented")

# unknown dispute outcome is a clean Rejected, not a raw KeyError
s, c, p = identified("Acme", weight=20.0)
c.disclose(p, "system", op(), ["ship"])
c.consent(p, "acme", op(), "ship", "exhibitor")
c.ship(p, "Peter", op(), "F", "T", "exhibitor", "s3://d", choice="ship")
c.open_dispute(p, "acme", op(), 52.0)
expect_raise("unknown dispute outcome rejected", Rejected,
             lambda: c.resolve_dispute(p, "Maria", op(), "banana", 52.0))


# --- atomic two-event commands: all-or-nothing (no partial write on a mid-command crash) ---
# happy path: a two-event command lands BOTH events
s, c, p = identified("James Chen")
c.disclose(p, "system", op(), ["pickup"])
c.abandon(p, "system", op(), "returned to sender")
_chain = [e["type"] for e in s.for_parcel(p)]
check("abandon wrote BOTH events (abandoned + closed)", "abandoned" in _chain and "closed" in _chain)
check("abandon reached closed", projections.parcel_state(s, p)["state"] == "closed")

# a failure BETWEEN the two emits rolls BOTH back — no orphan 'shipped', no lost 'billed'
s, c, p = identified("Acme", weight=20.0)
c.disclose(p, "system", op(), ["ship"])
c.consent(p, "acme", op(), "ship", "exhibitor")
_before = s.count()
_orig_append = s.append
_calls = {"n": 0}


def _boom(*a, **k):
    _calls["n"] += 1
    if _calls["n"] == 2:          # crash on the 2nd event (billed) of the ship pair
        raise RuntimeError("simulated crash mid-command")
    return _orig_append(*a, **k)


s.append = _boom
try:
    c.ship(p, "Peter", op(), "F", "T", "exhibitor", "s3://d", choice="ship")
    _raised = False
except RuntimeError:
    _raised = True
finally:
    s.append = _orig_append
check("atomic: mid-command failure propagated", _raised)
check("atomic: BOTH events rolled back (count unchanged)", s.count() == _before)
check("atomic: no orphan 'shipped' event", "shipped" not in [e["type"] for e in s.for_parcel(p)])
check("atomic: parcel still 'consented', not shipped", projections.parcel_state(s, p)["state"] == "consented")

# and a clean retry after the (simulated) crash completes normally
c.ship(p, "Peter", op(), "F", "T", "exhibitor", "s3://d", choice="ship")
check("atomic: retry after failure completes the ship", projections.parcel_state(s, p)["state"] == "shipped")

# --- segmentation + pricing (policy-as-data) ---
check("segment guest", policy.segment(contacts.CONTACTS["sarah"]) == "guest")
check("segment exhibitor", policy.segment(contacts.CONTACTS["acme"]) == "exhibitor")
check("guest pickup is free", policy.price_inbound("pickup")[0] == 0.0)
check("parcel price = base + per-lb", policy.price_outbound("parcel", 10) == 12.0 + 2.0 * 10)
check("freight + liftgate priced", policy.price_outbound("freight", 100, ("liftgate",)) ==
      round(75 + 0.35 * 100 + 60, 2))
check("auto-cover threshold", policy.auto_cover(40) and not policy.auto_cover(41))

# --- full happy path reaches closed; find + custody work ---
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1ZABC", "weight_lb": 6})
c.condition_photo(p, "Peter", op(), "s3://x")
c.apply_intent(agents.propose_identity(p, {"recipient_hint": "Sarah Chen"}), "Peter", op())
c.store_at(p, "Peter", op(), "TSC · MR Cage · C1", "s3://shelf")
c.disclose(p, "system", op(), ["pickup"])
c.release(p, "Desk", op(), "sig", choice="pickup", payer="guest")
c.close(p, "system", op())
st = projections.parcel_state(s, p)
check("happy path closed", st["state"] == "closed")
check("recipient identified", st["recipient"] == "Sarah Chen")
check("find-a-package by name", len(projections.find(s, "Sarah")) == 1)
check("find-a-package by tracking", len(projections.find(s, "1zabc")) == 1)
check("custody chain recorded", len(projections.custody_chain(s, p)) >= 6)

# --- lost/stolen: custody chain ends at drift/lost ---
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
c.capture(p, "Peter", op(), {"carrier": "UPS", "tracking": "1ZL", "weight_lb": 3})
c.apply_intent(agents.propose_identity(p, {"recipient_hint": "Sarah Chen"}), "Peter", op())
c.store_at(p, "Peter", op(), "TSC · MR Cage · C1")
c.flag_drift(p, "audit", op(), "missing")
check("drift state after audit flag", projections.parcel_state(s, p)["state"] == "drift")
c.open_theft(p, "M", op()); c.mark_lost(p, "M", op()); c.close(p, "M", op())
check("theft -> lost -> closed", projections.parcel_state(s, p)["state"] == "closed")

# --- reconcile COVER: a hotel-covered dispute REASSIGNS the charge, it does not add one ---
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Acme"})
c.capture(p, "Peter", op(), {"carrier": "F", "tracking": "T", "weight_lb": 20})
c.apply_intent(agents.propose_identity(p, {"recipient_hint": "Acme"}), "Peter", op())
c.store_at(p, "Peter", op(), "TSC · BC Cage · A2")
price = policy.price_outbound("parcel", 20)
c.disclose(p, "system", op(), ["ship"])
c.consent(p, "acme", op(), "ship", "exhibitor")   # core prices from captured weight (== price)
c.ship(p, "Peter", op(), "F", "T", "exhibitor", "s3://d", choice="ship")
c.open_dispute(p, "acme", op(), price)
c.resolve_dispute(p, "Maria", op(), "cover", price)   # hotel covers (also closes the parcel)
r = projections.reconcile(s)
check("reconcile cover: customer net 0 (charge moved off customer)", r["paid_by_customers"] == 0.0)
check("reconcile cover: hotel covered == price", r["hotel_covered"] == price)
check("reconcile cover: one covered case", r["hotel_covered_cases"] == 1)
check("reconcile cover: NO double-count (customer+hotel == one service)",
      round(r["paid_by_customers"] + r["hotel_covered"], 2) == price)

# --- projections are replayable & deterministic: two independent folds are identical ---
a, b = projections.parcel_state(s, p), projections.parcel_state(s, p)
check("replay deterministic (two folds identical)", a == b)
check("custody chain == full event count", len(a["custody"]) == len(s.for_parcel(p)))
check("replayed state is closed", a["state"] == "closed")

# --- reconcile DECLINE: dispute denied -> original customer still pays, exactly once ---
s, c = fresh()
p = c.arrive("Peter", op(), {"recipient_hint": "Acme"})
c.capture(p, "Peter", op(), {"carrier": "F", "tracking": "T2", "weight_lb": 20})
c.apply_intent(agents.propose_identity(p, {"recipient_hint": "Acme"}), "Peter", op())
c.store_at(p, "Peter", op(), "TSC · BC Cage · A3")
c.disclose(p, "system", op(), ["ship"])
c.consent(p, "acme", op(), "ship", "exhibitor")
c.ship(p, "Peter", op(), "F", "T2", "exhibitor", "s3://d", choice="ship")
c.open_dispute(p, "acme", op(), price)
c.resolve_dispute(p, "Maria", op(), "decline", price)   # denied -> exhibitor (not "guest") still owes
r = projections.reconcile(s)
check("reconcile decline: exhibitor still pays price", r["by_payer"]["exhibitor"] == price)
check("reconcile decline: not misrouted to guest", r["by_payer"]["guest"] == 0.0)
check("reconcile decline: hotel covers nothing", r["hotel_covered"] == 0.0)

# --- thread safety: concurrent writers + the unlocked-read path must not crash or lose events ---
s, c = fresh()
N = 24


def _worker(i):
    pid = c.arrive("t", op(), {"recipient_hint": "Sarah Chen"})
    c.capture(pid, "t", op(), {"carrier": "UPS", "tracking": "1Z%d" % i, "weight_lb": 5})
    projections.all_parcels(s)   # the read path that used to segfault under threads
    s.count()


_threads = [threading.Thread(target=_worker, args=(i,)) for i in range(N)]
for _t in _threads:
    _t.start()
for _t in _threads:
    _t.join()
check("concurrent writers: every parcel recorded", len(projections.all_parcels(s)) == N)
check("concurrent access: event count exact (no loss, no crash)", s.count() == N * 2)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
