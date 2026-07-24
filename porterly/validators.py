"""Semantic validators + the human-gate list.

The core validates the MEANING of a proposal, not just its shape: a weight must be
plausible, a confidence must clear a bar, and some actions are never autonomous.
"""

HUMAN_GATE = {"hazmat", "customs", "high_value", "address_change", "batch_gt_10"}

MIN_CONFIDENCE = 0.85
MAX_WEIGHT_LB = 3000.0


def plausible_weight(lb):
    try:
        return 0 < float(lb) <= MAX_WEIGHT_LB
    except (TypeError, ValueError):
        return False


def _norm(flag):
    """Canonicalize a flag so a safety gate can't be dodged by casing/spelling:
    'Hazmat', 'HAZMAT', 'high-value', 'high value' all collapse to the gate key."""
    return str(flag).strip().lower().replace("-", "_").replace(" ", "_")


def needs_human(flags):
    return bool({_norm(f) for f in (flags or ())} & HUMAN_GATE)


def confident(conf):
    return conf is not None and conf >= MIN_CONFIDENCE
