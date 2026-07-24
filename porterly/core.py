"""The deterministic Core — the ONLY component that changes state / money / shipping.

Every command: (1) reads current state, (2) guards the FSM transition, (3) runs
semantic validators + human gates, (4) appends the event idempotently. Agents feed
it structured ProposedIntents through apply_intent(); nothing else can act.

`_emit` is the single choke point — there is exactly one place in the whole system
where a fact gets written, and it is guarded.
"""
from . import contacts, fsm, policy, projections
from . import validators as V
from .agents import ProposedIntent


class Rejected(Exception):
    """The core refused a proposal (low confidence, human gate, bad transition)."""


class Core:
    def __init__(self, store):
        self.store = store

    def _state(self, parcel_id):
        return projections.parcel_state(self.store, parcel_id)["state"]

    def _emit(self, parcel_id, type, data, actor, op):
        key = "%s:%s:%s" % (parcel_id, type, op)
        # The read-guard-write below must be ATOMIC: without this, two concurrent
        # distinct commands on the same parcel could both read the same state, both
        # pass the FSM guard, and both append — persisting an illegal transition pair
        # into the append-only log. The store's re-entrant lock makes the whole
        # sequence a single-writer critical section (the nested store calls re-enter).
        with self.store.lock:
            # 1) idempotency FIRST: a retry is already-applied — return it, don't re-guard.
            #    But a reused op carrying DIFFERENT data is a key collision, not a retry —
            #    refuse it instead of silently returning the stale event.
            existing = self.store.get_by_key(key)
            if existing is not None:
                if existing["data"] != data:
                    raise Rejected("idempotency collision: op reused for a different %s" % type)
                return existing, False
            # 2) guard the transition for genuinely-new events (raises before any write)
            fsm.next_state(self._state(parcel_id), type)
            # 3) append
            return self.store.append(parcel_id, type, data, actor, key)

    def _emit_atomic(self, parcel_id, actor, steps):
        """Emit several events as ONE transaction: all land or none do. Closes the
        partial-write window on multi-event commands — a crash mid-command can no longer
        leave 'released' without 'billed', or 'abandoned' without 'closed'. Each new event
        is FSM-guarded in order, and because the staged (uncommitted) rows are visible to
        reads on the same connection, a later step's guard correctly sees the earlier ones.
        The store's re-entrant lock keeps the whole batch a single isolated transaction.
        steps: [(type, data, op), ...]; returns [(event, created), ...] aligned to steps."""
        with self.store.lock:
            try:
                out = []
                for etype, data, op in steps:
                    key = "%s:%s:%s" % (parcel_id, etype, op)
                    existing = self.store.get_by_key(key)
                    if existing is not None:          # idempotent retry — already applied
                        if existing["data"] != data:
                            raise Rejected("idempotency collision: op reused for a different %s" % etype)
                        out.append((existing, False))
                        continue
                    fsm.next_state(self._state(parcel_id), etype)   # guard (raises -> rollback)
                    out.append(self.store.append(parcel_id, etype, data, actor, key, commit=False))
                self.store.commit()
                return out
            except Exception:
                self.store.rollback()   # any failure mid-batch -> no partial write
                raise

    # ---------- perception -> CORROBORATE -> execute ----------
    def apply_intent(self, intent, actor, op):
        """Propose -> CORROBORATE -> execute. A proposal is DATA, not authority: the core
        does NOT trust the proposer's self-reported confidence or fields. It re-derives the
        identity from the trusted directory using the observation recorded at arrival, and
        writes the directory's authoritative fields. A forged recipient_id, a hallucinated
        confidence, or a tampered master_account cannot get past this."""
        if not isinstance(intent, ProposedIntent):     # the core acts only on a typed proposal
            raise Rejected("apply_intent requires a ProposedIntent, got %r" % type(intent).__name__)
        if V.needs_human(intent.flags):     # if perception RAISES a gate, honor it (fail-safe)
            raise Rejected("human gate required: %s" % sorted(set(intent.flags)))
        if intent.kind == "identify":
            return self._identify(intent, actor, op)
        raise Rejected("unknown intent kind: %r" % intent.kind)

    def _observed_hint(self, parcel_id):
        """The observation recorded at arrival, before any agent touched the parcel — the
        core's independent ground truth for corroborating an identity claim. (In production
        this is the intake label/OCR text; here, the recipient_hint captured at arrival.)"""
        for e in self.store.for_parcel(parcel_id):
            if e["type"] == "arrived":
                return (e["data"].get("label") or {}).get("recipient_hint")
        return None

    def _identify(self, intent, actor, op):
        # 1) the core INDEPENDENTLY matches the arrival observation against the directory —
        #    the proposer's match_confidence is never trusted; this confidence is.
        record, confidence = contacts.match(self._observed_hint(intent.parcel_id))
        if record is None or not V.confident(confidence):
            raise Rejected("identity not corroborated by directory (%.2f) — escalate to a human"
                           % (confidence or 0.0))
        # 2) the proposer's claim, if present, must AGREE with the directory match (no forging)
        claimed = intent.fields.get("recipient_id")
        if claimed and claimed != record["id"]:
            raise Rejected("proposed recipient %r contradicts directory %r — escalate"
                           % (claimed, record["id"]))
        # 3) write the directory's AUTHORITATIVE fields, never the proposer's copy
        fields = {
            "recipient_id": record["id"], "recipient": record["name"], "room": record.get("room"),
            "kind": record["kind"], "company": record.get("company"),
            "master_account": record.get("master_account"),
        }
        return self._emit(intent.parcel_id, "identified", fields, actor, op)

    # ---------- inbound ----------
    def arrive(self, actor, op, label):
        parcel_id = "PR-" + op[:10]
        self._emit(parcel_id, "arrived", {"label": label}, actor, op)
        return parcel_id

    def capture(self, pid, actor, op, label):
        if not V.plausible_weight(label.get("weight_lb", 1)):
            raise Rejected("implausible weight %r — recheck / manual bay" % label.get("weight_lb"))
        return self._emit(pid, "captured", {
            "carrier": label.get("carrier"), "tracking": label.get("tracking"),
            "weight_lb": label.get("weight_lb"), "dims": label.get("dims"),
        }, actor, op)

    def condition_photo(self, pid, actor, op, uri):
        return self._emit(pid, "condition_captured", {"photo": uri}, actor, op)

    def store_at(self, pid, actor, op, location, shelf_photo=None):
        return self._emit(pid, "stored", {"location": location, "shelf_photo": shelf_photo}, actor, op)

    def disclose(self, pid, actor, op, options):
        return self._emit_atomic(pid, actor, [
            ("notified", {"options": options}, op + "-n"),
            ("disclosed", {"options": options}, op),
        ])[-1]

    # ---------- pricing & the consent guard (policy-as-data, enforced HERE) ----------
    def _price(self, pid, choice, accessorials=()):
        """Authoritative price for a service — computed by the core FROM POLICY, never a
        number the caller supplies. Returns (amount, item). This is what makes pricing
        enforced rather than advisory."""
        if choice in ("pickup", "room"):
            return policy.price_inbound(choice)
        weight = projections.parcel_state(self.store, pid).get("weight_lb") or 0
        mode = "freight" if choice == "freight" else "parcel"
        return policy.price_outbound(mode, weight, accessorials), "outbound"

    def _consent_record(self, pid):
        rec = None
        for e in self.store.for_parcel(pid):
            if e["type"] == "consented":
                rec = e["data"]          # last consent wins
        return rec

    def _assert_consent_covers(self, pid, amount, payer):
        """The no-ambush guarantee: a customer is never billed more than they agreed to.
        $0 (the free amenity) and non-customer payers (hotel/porterly, from a dispute) are
        exempt — there is nothing to ambush. Raises BEFORE any event is written."""
        if amount <= 0 or payer not in ("guest", "exhibitor"):
            return
        rec = self._consent_record(pid)
        if rec is None:
            raise Rejected("no-ambush: $%.2f to %s requires prior consent" % (amount, payer))
        if rec.get("payer") != payer:
            raise Rejected("consent payer %r != charge payer %r" % (rec.get("payer"), payer))
        if round(amount - float(rec.get("amount") or 0.0), 2) > 0:
            raise Rejected("no-ambush: $%.2f exceeds consented $%.2f" % (amount, rec.get("amount") or 0.0))

    def consent(self, pid, actor, op, choice, payer, accessorials=()):
        """Record what the customer agreed to — at the POLICY price the core computes,
        not a number the caller passes. Returns the priced amount (for display)."""
        amount, _ = self._price(pid, choice, accessorials)
        self._emit(pid, "consented",
                   {"choice": choice, "amount": amount, "payer": payer}, actor, op)
        return amount

    def release(self, pid, actor, op, proof, choice="pickup", payer="guest"):
        amount, item = self._price(pid, choice)
        self._assert_consent_covers(pid, amount, payer)      # guard BEFORE any write
        return self._emit_atomic(pid, actor, [
            ("released", {"proof": proof}, op),
            ("billed", {"amount": amount, "payer": payer, "item": item}, op + "-b"),
        ])[-1]

    def ship(self, pid, actor, op, carrier, tracking, payer, departure_photo, choice="ship", accessorials=()):
        amount, _ = self._price(pid, choice, accessorials)
        self._assert_consent_covers(pid, amount, payer)      # guard BEFORE any write
        return self._emit_atomic(pid, actor, [
            ("shipped", {"carrier": carrier, "tracking": tracking, "departure_photo": departure_photo}, op),
            ("billed", {"amount": amount, "payer": payer, "item": "outbound"}, op + "-b"),
        ])[-1]

    def in_transit(self, pid, actor, op, status):
        return self._emit(pid, "in_transit", {"status": status}, actor, op)

    def deliver(self, pid, actor, op):
        return self._emit(pid, "delivered", {}, actor, op)

    def close(self, pid, actor, op):
        return self._emit(pid, "closed", {}, actor, op)

    # ---------- exceptions ----------
    def flag_drift(self, pid, actor, op, note):
        return self._emit(pid, "drift_flagged", {"note": note}, actor, op)

    def found(self, pid, actor, op, location):
        return self._emit(pid, "found", {"location": location}, actor, op)

    def open_theft(self, pid, actor, op):
        return self._emit(pid, "theft_opened", {}, actor, op)

    def mark_lost(self, pid, actor, op):
        return self._emit(pid, "lost", {}, actor, op)

    def abandon(self, pid, actor, op, disposition):
        return self._emit_atomic(pid, actor, [
            ("abandoned", {"disposition": disposition}, op),
            ("closed", {"via": "abandoned"}, op + "-c"),
        ])[-1]

    def open_dispute(self, pid, actor, op, amount):
        return self._emit(pid, "dispute_opened", {"amount": amount}, actor, op)

    def resolve_dispute(self, pid, actor, op, outcome, amount):
        if outcome not in ("cover", "partial", "absorb", "decline"):
            raise Rejected("unknown dispute outcome: %r" % outcome)
        # The customer originally invoiced on this parcel — relieved when the hotel
        # covers or Porterly absorbs; kept when the dispute is declined.
        billed = [c for c in projections.parcel_state(self.store, pid)["charges"]
                  if not c["item"].startswith("dispute:") and c.get("amount")]
        original_payer = billed[-1]["payer"] if billed else "guest"
        # A resolution REASSIGNS the disputed amount; it is a transfer, not a second
        # charge. "decline" leaves it on whoever was originally billed (guest OR exhibitor).
        final_payer = {"cover": "hotel", "partial": "hotel", "absorb": "porterly",
                       "decline": original_payer}[outcome]
        return self._emit(pid, "dispute_resolved",
                          {"outcome": outcome, "payer": final_payer,
                           "original_payer": original_payer, "amount": amount}, actor, op)
