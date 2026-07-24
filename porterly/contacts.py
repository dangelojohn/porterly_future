"""Contacts — the recipient directory (guests + exhibitors).

In production this syncs from the hotel PMS + event/booth data, and the match is a
vector search. Here it's a seeded dict + a toy matcher that returns a confidence,
so the propose->validate path (which escalates on low confidence / ambiguity) can
be exercised for real.
"""

CONTACTS = {
    "sarah": {"id": "sarah", "name": "Sarah Chen", "room": "412", "kind": "guest", "company": "TSC"},
    "ellis": {"id": "ellis", "name": "Mr. Ellis", "room": "224", "kind": "guest", "company": "TSC"},
    "jchen": {"id": "jchen", "name": "James Chen", "room": "905", "kind": "guest", "company": "TSC"},
    "acme":  {"id": "acme", "name": "Acme Robotics", "kind": "exhibitor", "event": "TechExpo",
              "booth": "214", "company": "TSC", "master_account": "MA-ACME"},
}


def match(hint):
    """Return (record_or_None, confidence). Ambiguous (>1) => low confidence => escalate."""
    h = (hint or "").strip().lower()
    if not h:
        return None, 0.0
    hits = [c for c in CONTACTS.values()
            if h in c["name"].lower() or h == c.get("room")]
    if len(hits) == 1:
        return hits[0], 0.98
    if len(hits) > 1:
        return None, 0.45          # e.g. "chen" matches Sarah + James -> escalate
    return None, 0.0
