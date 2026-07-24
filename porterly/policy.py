"""Policy-as-DATA — pricing, segmentation, gates.

The business rules live here as configuration, not scattered code branches. A new
hotel or a price change is an edit to this dict (or its DB-backed equivalent), not
a code fork (the fix for the hardcoded-"TSC" legacy bug).
"""

POLICY = {
    "consent_timeout_hours": 24,
    "hold_period_days": 60,
    "auto_cover_max": 40.0,          # hotel auto-covers disputes at/under this
    "inbound": {
        "guest_pickup": 0.0,          # a free amenity — the whole point
        "guest_room_delivery": 8.0,
    },
    "outbound": {
        "parcel_base": 12.0, "parcel_per_lb": 2.0,
        "freight_base": 75.0, "freight_per_lb": 0.35,
        "accessorials": {"liftgate": 60.0, "inside_pickup": 90.0},
    },
}


def segment(recipient):
    """Guest incidental mail vs exhibitor freight — decided from the recipient
    record at capture. This single distinction is 80% of the fix."""
    return "exhibitor" if (recipient or {}).get("kind") == "exhibitor" else "guest"


def price_inbound(choice):
    inb = POLICY["inbound"]
    if choice == "pickup":
        return round(inb["guest_pickup"], 2), "guest_pickup"
    if choice == "room":
        return round(inb["guest_room_delivery"], 2), "guest_room_delivery"
    raise ValueError("unknown inbound choice: %r" % (choice,))   # never silently free


def price_outbound(mode, weight_lb, accessorials=()):
    if mode not in ("parcel", "freight"):
        raise ValueError("unknown outbound mode: %r" % (mode,))   # never silently mis-price
    try:
        w = float(weight_lb)
    except (TypeError, ValueError):
        raise ValueError("weight_lb must be a number, got %r" % (weight_lb,))
    if w <= 0:
        raise ValueError("weight_lb must be positive, got %r" % (weight_lb,))
    o = POLICY["outbound"]
    base, per_lb = ((o["freight_base"], o["freight_per_lb"]) if mode == "freight"
                    else (o["parcel_base"], o["parcel_per_lb"]))
    amt = base + per_lb * w
    for a in accessorials:
        if a not in o["accessorials"]:
            raise ValueError("unknown accessorial: %r" % (a,))    # never silently free
        amt += o["accessorials"][a]
    return round(amt, 2)


def auto_cover(amount):
    return amount is not None and 0 <= amount <= POLICY["auto_cover_max"]
