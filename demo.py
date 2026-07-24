#!/usr/bin/env python3
"""End-to-end demo — walk plausible packages through the whole spine and print it.

    python3 demo.py

Proves, concretely: capture -> propose -> validate -> execute, the guarded FSM,
idempotency, the guest/exhibitor split, disclosure+consent, the lost/stolen
investigation, guardrail rejections, and the reconcile-that-kills-the-meeting.
"""
import uuid

from porterly.core import Core, Rejected
from porterly.fsm import InvalidTransition
from porterly.ledger import EventStore
from porterly import agents, policy, projections


def op():
    return uuid.uuid4().hex


def hr(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


def show(store, pid, tail=None):
    st = projections.parcel_state(store, pid)
    print("  %-9s  state=%-16s  %s%s" % (
        pid, st["state"], st["recipient"] or "?",
        (" [" + st["kind"] + "]") if st["kind"] else ""))
    if tail:
        for c in projections.custody_chain(store, pid)[-tail:]:
            print("      %s  %-16s %s" % (c["ts"][11:16], c["type"], c["actor"]))


store = EventStore()          # in-memory SQLite
core = Core(store)


def receive_and_identify(hint, actor="Peter", carrier="UPS", weight=6.0, flags=()):
    """Common inbound head: arrive -> capture -> condition -> propose -> validate."""
    o = op()
    pid = core.arrive(actor, o, {"recipient_hint": hint})
    core.capture(pid, actor, op(), {"carrier": carrier, "tracking": "1Z" + o[:10].upper(),
                                    "weight_lb": weight, "dims": "18x12x10", "flags": list(flags)})
    core.condition_photo(pid, actor, op(), "s3://evidence/%s/intake.jpg" % pid)
    intent = agents.propose_identity(pid, {"recipient_hint": hint, "flags": list(flags)})
    core.apply_intent(intent, actor, op())          # raises Rejected if not confident / gated
    return pid


# ----------------------------------------------------------------------
hr("1 · GUEST small box  ->  free desk pickup (the amenity)")
pid = receive_and_identify("Sarah Chen", weight=6.0)
core.store_at(pid, "Peter", op(), "TSC · MR Cage · C1", "s3://.../shelf.jpg")
core.disclose(pid, "system", op(), ["pickup(free)", "room($8)", "ship"])
core.release(pid, "Front desk", op(), "signature:sc", choice="pickup", payer="guest")   # core prices it $0
core.close(pid, "system", op())
show(store, pid, tail=4)
amt = projections.parcel_state(store, pid)["charges"][-1]["amount"]
print("  -> billed $%.2f (free amenity) — the guest is never ambushed." % amt)

# ----------------------------------------------------------------------
hr("2 · GUEST postcard  ->  also free (weight/size don't create a fee)")
pid = receive_and_identify("Mr. Ellis", weight=0.1)
core.store_at(pid, "Peter", op(), "TSC · Mailroom · Bin 3")
core.disclose(pid, "system", op(), ["pickup(free)", "room($8)", "ship"])
core.release(pid, "Front desk", op(), "signature:me", choice="pickup", payer="guest")
core.close(pid, "system", op())
show(store, pid, tail=2)

# ----------------------------------------------------------------------
hr("3 · EXHIBITOR parcel outbound  ->  bill to the master account")
o = op(); pid = core.arrive("Peter", o, {"recipient_hint": "Acme"})
core.capture(pid, "Peter", op(), {"carrier": "FedEx", "tracking": "T-" + o[:8], "weight_lb": 22.0, "dims": "24x18x18"})
core.condition_photo(pid, "Peter", op(), "s3://.../intake.jpg")
core.apply_intent(agents.propose_identity(pid, {"recipient_hint": "Acme"}), "Peter", op())
core.store_at(pid, "Peter", op(), "TSC · BC Cage · A2")
core.disclose(pid, "system", op(), ["ship"])
price = core.consent(pid, "acme", op(), "ship", "exhibitor")   # core prices from captured weight
core.ship(pid, "Peter", op(), "FedEx", "794-XX", "exhibitor", "s3://.../departure.jpg", choice="ship")
core.in_transit(pid, "system", op(), "picked up")
core.deliver(pid, "system", op()); core.close(pid, "system", op())
show(store, pid, tail=5)
print("  -> $%.2f to the exhibitor's master account (expected & budgeted)." % price)

# ----------------------------------------------------------------------
hr("4 · SILENT guest  ->  consent times out  ->  lawful return (no orphan)")
pid = receive_and_identify("James Chen")
core.store_at(pid, "Peter", op(), "TSC · MR Cage · G1")
core.disclose(pid, "system", op(), ["pickup(free)", "ship"])
# ...24h passes, no reply...
core.abandon(pid, "system", op(), "returned to sender")
show(store, pid, tail=3)

# ----------------------------------------------------------------------
hr("5 · FREIGHT pallet outbound  ->  priced with accessorials")
o = op(); pid = core.arrive("Peter", o, {"recipient_hint": "Acme"})
core.capture(pid, "Peter", op(), {"carrier": "LTL", "tracking": "BOL-" + o[:6], "weight_lb": 900.0, "dims": "48x40x60"})
core.apply_intent(agents.propose_identity(pid, {"recipient_hint": "Acme"}), "Peter", op())
core.store_at(pid, "Peter", op(), "TSC · Floor · P1")
core.disclose(pid, "system", op(), ["freight(liftgate)"])
freight = core.consent(pid, "acme", op(), "freight", "exhibitor", accessorials=("liftgate",))
core.ship(pid, "Peter", op(), "LTL", "PRO-55", "exhibitor", "s3://.../pallet.jpg",
          choice="freight", accessorials=("liftgate",))
show(store, pid, tail=3)
print("  -> $%.2f (freight base + per-lb + liftgate) — Porterly = shipper-of-record." % freight)

# ----------------------------------------------------------------------
hr("6 · LOST / STOLEN investigation  ->  a search, then an evidence trail")
pid = receive_and_identify("Sarah Chen", weight=3.0)
core.store_at(pid, "Peter", op(), "TSC · MR Cage · C1", "s3://.../shelf.jpg")
core.disclose(pid, "system", op(), ["pickup(free)", "ship"])
core.flag_drift(pid, "drift-audit", op(), "not on shelf at nightly sweep")
print("  Find-a-package 'Sarah Chen' ->")
for p in projections.find(store, "Sarah Chen"):
    if p["state"] == "drift":
        print("    %s  last-known=%s" % (p["parcel_id"], p["location"]))
        for c in projections.custody_chain(store, p["parcel_id"]):
            print("      %s  %-16s %s" % (c["ts"][11:16], c["type"], c["actor"]))
core.open_theft(pid, "Maria", op())
core.mark_lost(pid, "Maria", op())
core.close(pid, "Maria", op())
show(store, pid)
print("  -> custody chain ends at a precise time+place+person: the evidence trail.")

# ----------------------------------------------------------------------
hr("7 · DISPUTES  ->  auto-cover by policy vs. a manager decision")
# small dispute (<= auto_cover_max) settles by policy, hotel covers
pid = receive_and_identify("Mr. Ellis", weight=4.0)
core.store_at(pid, "Peter", op(), "TSC · MR Cage · C2")
core.disclose(pid, "system", op(), ["room($8)"])
core.consent(pid, "ellis", op(), "room", "guest")
core.release(pid, "Front desk", op(), "signature", choice="room", payer="guest")
core.open_dispute(pid, "ellis", op(), 8.0)
if policy.auto_cover(8.0):
    core.resolve_dispute(pid, "auto-policy", op(), "cover", 8.0)   # resolving also closes the parcel
    print("  $8 dispute  -> AUTO-COVERED by policy (<= $%.0f). No human needed." % policy.POLICY["auto_cover_max"])

# big dispute (> auto_cover_max) -> goes to the manager's queue for a real call
o = op(); pid = core.arrive("Peter", o, {"recipient_hint": "Acme"})
core.capture(pid, "Peter", op(), {"carrier": "LTL", "tracking": "BOL-" + o[:6], "weight_lb": 120.0, "dims": "40x30x30"})
core.apply_intent(agents.propose_identity(pid, {"recipient_hint": "Acme"}), "Peter", op())
core.store_at(pid, "Peter", op(), "TSC · Floor · P2")
core.disclose(pid, "system", op(), ["freight"])
big = core.consent(pid, "acme", op(), "freight", "exhibitor")
core.ship(pid, "Peter", op(), "LTL", "PRO-77", "exhibitor", "s3://.../d.jpg", choice="freight")
core.open_dispute(pid, "acme", op(), big)
print("  $%.2f dispute -> exceeds $%.0f, routed to MANAGER queue -> decision: Cover" %
      (big, policy.POLICY["auto_cover_max"]))
core.resolve_dispute(pid, "Maria", op(), "cover", big)   # manager decides in the moment (also closes)

# ----------------------------------------------------------------------
hr("8 · GUARDRAILS  ->  the core refuses bad proposals AND bad charges")
# set up parcels that will FAIL at the decision / billing step
p_amb = core.arrive("Peter", op(), {"recipient_hint": "chen"})
core.capture(p_amb, "Peter", op(), {"carrier": "UPS", "tracking": "1Zamb", "weight_lb": 5})
p_haz = core.arrive("Peter", op(), {"recipient_hint": "Sarah Chen"})
core.capture(p_haz, "Peter", op(), {"carrier": "UPS", "tracking": "1Zhz", "weight_lb": 5})
# an identified, stored, disclosed guest — awaiting consent, but NEVER consents to the $8 fee
p_amb2 = receive_and_identify("Sarah Chen", weight=4.0)
core.store_at(p_amb2, "Peter", op(), "TSC · MR Cage · C4")
core.disclose(p_amb2, "system", op(), ["room($8)"])
guard_before = store.count()                 # measure ONLY the rejected/blocked ops below
try:  # a) ambiguous recipient ("chen" matches Sarah + James) -> low confidence
    core.apply_intent(agents.propose_identity(p_amb, {"recipient_hint": "chen"}), "Peter", op())
except Rejected as e:
    print("  ambiguous 'chen'         -> REJECTED: %s" % e)
try:  # b) hazmat -> hard human gate, zero autonomy
    core.apply_intent(agents.propose_identity(p_haz, {"recipient_hint": "Sarah Chen", "flags": ["hazmat"]}), "Peter", op())
except Rejected as e:
    print("  hazmat flag              -> REJECTED: %s" % e)
try:  # c) illegal FSM transition -> guarded (free pickup, so it's the FSM that refuses, not consent)
    core.release(p_haz, "Peter", op(), "sig", choice="pickup", payer="guest")   # can't release a just-captured parcel
except InvalidTransition as e:
    print("  release-before-store     -> BLOCKED: %s" % e)
try:  # d) the no-ambush guard: a paid charge with no matching consent is refused
    core.release(p_amb2, "Front desk", op(), "sig", choice="room", payer="guest")   # $8, never consented
except Rejected as e:
    print("  $8 fee, no consent       -> REJECTED: %s" % e)
print("  -> the 4 rejected/blocked ops wrote %d events (nothing gets past the core)."
      % (store.count() - guard_before))

# ----------------------------------------------------------------------
hr("9 · IDEMPOTENCY  ->  a retried command writes once")
o = op(); pid = core.arrive("Peter", o, {"recipient_hint": "Sarah Chen"})
c1 = store.count()
myop = op()
_, made1 = core.capture(pid, "Peter", myop, {"carrier": "UPS", "tracking": "1Zdup", "weight_lb": 5})
_, made2 = core.capture(pid, "Peter", myop, {"carrier": "UPS", "tracking": "1Zdup", "weight_lb": 5})  # retry
print("  first capture created=%s ; retry created=%s ; events added=%d (expected 1)"
      % (made1, made2, store.count() - c1))

# ----------------------------------------------------------------------
hr("10 · RECONCILE  ->  the settled statement (replaces the monthly meeting)")
r = projections.reconcile(store)
print("  customers paid   : $%.2f" % r["paid_by_customers"])
print("  hotel covered    : $%.2f  across %d case(s)" % (r["hotel_covered"], r["hotel_covered_cases"]))
print("  porterly absorbed: $%.2f" % r["porterly_absorbed"])
print("  by payer         : %s" % r["by_payer"])
print("  storage twin     : %s" % projections.storage_map(store))
print("\n  Every case already decided & booked. Nothing left to reconcile.\n")
