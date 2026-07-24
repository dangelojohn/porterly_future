"""Read models — folded from the event stream, always rebuildable (replayable).

The storage_map is the digital twin of the shelf; find() powers the manager's
"find a package"; custody_chain() is the immutable investigation trail; reconcile()
is the settled statement that replaces the monthly meeting.
"""
from . import fsm


def _fold(events):
    st = {
        "state": None, "parcel_id": None, "recipient": None, "room": None, "kind": None,
        "company": None, "location": None, "tracking": None, "weight_lb": None,
        "master_account": None, "charges": [], "custody": [],
    }
    for e in events:
        st["parcel_id"] = e["parcel_id"]
        t, d = e["type"], e["data"]
        try:
            st["state"] = fsm.next_state(st["state"], t)
        except fsm.InvalidTransition:
            pass  # stored events are already legal; be defensive on rebuild
        st["custody"].append({"ts": e["ts"], "type": t, "actor": e["actor"], "data": d})
        if t == "captured":
            st["tracking"] = d.get("tracking"); st["weight_lb"] = d.get("weight_lb")
        elif t == "identified":
            for k in ("recipient", "room", "kind", "company", "master_account"):
                if d.get(k) is not None:
                    st[k] = d.get(k)
        elif t in ("stored", "found"):
            st["location"] = d.get("location")
        elif t == "billed":
            st["charges"].append({"amount": d["amount"], "payer": d["payer"], "item": d["item"]})
        elif t == "dispute_resolved":
            st["charges"].append({"amount": d.get("amount"), "payer": d["payer"],
                                  "original_payer": d.get("original_payer"),
                                  "item": "dispute:" + d["outcome"]})
    return st


def parcel_state(store, parcel_id):
    return _fold(store.for_parcel(parcel_id))


def all_parcels(store):
    seen, order = set(), []
    for e in store.all():
        if e["parcel_id"] not in seen:
            seen.add(e["parcel_id"]); order.append(e["parcel_id"])
    return [parcel_state(store, pid) for pid in order]


IN_STORAGE = {"stored", "awaiting_consent", "consented", "drift", "theft_review"}
# physically received but not yet shelved/triaged (e.g. an identity-rejected parcel sitting
# in the intake bay) — these have NO shelf location yet, but they ARE in the building.
RECEIVED_UNSHELVED = {"arrived", "captured", "identified"}
INTAKE_BUCKET = "· intake — awaiting triage ·"


def storage_map(store):
    """The digital twin of what is physically here. Shelved parcels appear under their
    location; received-but-unshelved parcels appear under a single intake bucket, so nothing
    physically in the building is invisible to a manager scanning the twin."""
    m = {}
    for p in all_parcels(store):
        if p["state"] in IN_STORAGE and p["location"]:
            m.setdefault(p["location"], []).append(p["parcel_id"])
        elif p["state"] in RECEIVED_UNSHELVED:
            m.setdefault(INTAKE_BUCKET, []).append(p["parcel_id"])
    return m


def find(store, query):
    q = (query or "").strip().lower()
    out = []
    for p in all_parcels(store):
        hay = " ".join(str(p.get(k) or "") for k in ("recipient", "room", "tracking", "parcel_id")).lower()
        if q and q in hay:
            out.append(p)
    return out


def custody_chain(store, parcel_id):
    return parcel_state(store, parcel_id)["custody"]


def reconcile(store):
    tot = {"guest": 0.0, "exhibitor": 0.0, "hotel": 0.0, "porterly": 0.0}
    covered_cases, reasons = 0, {}
    for p in all_parcels(store):
        for c in p["charges"]:
            amt = c["amount"] or 0.0
            if c["item"].startswith("dispute:"):
                # A resolved dispute REASSIGNS the disputed amount — it relieves the
                # customer originally invoiced and moves it to the final payer. This is
                # a transfer, NOT a second charge; summing both would double-count every
                # covered dispute (overstate customer payments AND total revenue).
                orig, final = c.get("original_payer"), c["payer"]
                if orig and orig != final:
                    tot[orig] = round(tot.get(orig, 0.0) - amt, 2)
                    tot[final] = round(tot.get(final, 0.0) + amt, 2)
                elif not orig and amt:
                    tot[final] = round(tot.get(final, 0.0) + amt, 2)
                if final == "hotel":
                    covered_cases += 1
                reasons[c["item"]] = reasons.get(c["item"], 0) + 1
            elif amt:
                tot[c["payer"]] = round(tot.get(c["payer"], 0.0) + amt, 2)
    return {
        "paid_by_customers": round(tot["guest"] + tot["exhibitor"], 2),
        "hotel_covered": round(tot["hotel"], 2),
        "hotel_covered_cases": covered_cases,
        "porterly_absorbed": round(tot["porterly"], 2),
        "by_payer": tot,
        "dispute_reasons": reasons,
    }
