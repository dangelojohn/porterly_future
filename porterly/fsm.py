"""The parcel Finite State Machine — guarded transitions.

State is NOT a mutable field anyone can poke (the legacy render-mutation /
scattered-state bug). It is the fold of an event stream, and every transition is
guarded here. An illegal transition raises before anything is written.
"""


class InvalidTransition(Exception):
    pass


# Events that annotate a parcel without changing its state.
NON_TRANSITIONING = {"condition_captured", "notified"}

# Annotations that don't change state but ARE guarded to a set of states — so the money
# event ('billed') can't be recorded from an arbitrary state (it must follow release/ship).
GUARDED_ANNOTATION = {"billed": {"released", "shipped"}}

# event_type -> (allowed_from_states, resulting_state).  None in allowed_from = creation.
_T = {
    "arrived":          ({None}, "arrived"),
    "captured":         ({"arrived"}, "captured"),
    "identified":       ({"captured"}, "identified"),
    "stored":           ({"identified", "drift"}, "stored"),
    "disclosed":        ({"stored"}, "awaiting_consent"),
    "released":         ({"awaiting_consent", "consented"}, "released"),
    "consented":        ({"awaiting_consent"}, "consented"),
    "shipped":          ({"consented", "awaiting_consent"}, "shipped"),
    "in_transit":       ({"shipped", "in_transit"}, "in_transit"),
    "delivered":        ({"shipped", "in_transit"}, "delivered"),
    "closed":           ({"released", "delivered", "disputed", "abandoned", "lost"}, "closed"),
    "drift_flagged":    ({"stored", "awaiting_consent", "consented"}, "drift"),
    "found":            ({"drift", "theft_review"}, "stored"),
    "theft_opened":     ({"drift", "stored"}, "theft_review"),
    "lost":             ({"theft_review"}, "lost"),
    "abandoned":        ({"awaiting_consent", "stored"}, "abandoned"),
    "dispute_opened":   ({"released", "shipped", "delivered", "consented"}, "disputed"),
    "dispute_resolved": ({"disputed"}, "closed"),
}

STATES = sorted({s for spec in _T.values() for s in spec[1:]} |
                {s for spec in _T.values() for s in spec[0] if s})


def next_state(current, event_type):
    if event_type in NON_TRANSITIONING:
        return current
    if event_type in GUARDED_ANNOTATION:
        if current not in GUARDED_ANNOTATION[event_type]:
            raise InvalidTransition("cannot %s from state %r" % (event_type, current))
        return current
    spec = _T.get(event_type)
    if spec is None:
        raise InvalidTransition("unknown event type: %r" % event_type)
    allowed, to = spec
    if current not in allowed:
        raise InvalidTransition("cannot %s from state %r" % (event_type, current))
    return to
