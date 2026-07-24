"""The propose-only boundary.

An agent's whole job is to turn a messy observation (a label, a chat message) into
ONE structured ProposedIntent — and then stop. It calls no mutation tool. The
deterministic Core is the only thing that acts. This is what makes a prompt-injected
label harmless: at worst it produces a *proposal* the Core then rejects.

In production `propose_identity` is a Claude call that reads a photo/label and
disambiguates against Contacts; here it's a deterministic matcher with the same
shape (a confidence the Core gates on). Swapping in the LLM changes nothing
downstream — that is the point of the seam.
"""
from dataclasses import dataclass, field

from . import contacts


@dataclass
class ProposedIntent:
    kind: str
    parcel_id: str
    fields: dict
    match_confidence: float = 1.0
    flags: list = field(default_factory=list)
    source: str = "rules"      # "rules" (fast-path) | "agent" (LLM)


def propose_identity(parcel_id, label):
    """Perception: who is this for, and are they a guest or an exhibitor?"""
    rec, conf = contacts.match(label.get("recipient_hint"))
    fields = {}
    if rec:
        fields = {
            "recipient_id": rec["id"], "recipient": rec["name"], "room": rec.get("room"),
            "kind": rec["kind"], "company": rec["company"],
            "master_account": rec.get("master_account"),
        }
    # >=0.99 would be the deterministic fast-path; anything less is the "agent" lane.
    return ProposedIntent(kind="identify", parcel_id=parcel_id, fields=fields,
                          match_confidence=conf,
                          source="rules" if conf >= 0.99 else "agent",
                          flags=list(label.get("flags", [])))
