# Reference build — deep code review (2026-07-23)

Five independent adversarial reviewers (one per dimension: state-machine/core, ledger/idempotency/replay,
policy/pricing/validators, HTTP surface/test-quality, architecture-integrity), each verified against the
source by hand before anything was changed. Findings below are de-duplicated and severity-ranked.

## Critical — FIXED

| # | Finding | Confirmed by | Fix |
|---|---------|--------------|-----|
| C1 | **`reconcile()` double-counts covered disputes.** A resolution appended a *second* charge instead of *reassigning* the original, so a $117 exhibitor charge the hotel covered was booked as $117 exhibitor-paid **and** $117 hotel-covered ($234 for one $117 job). Present for every `cover`/`decline`/`absorb`/`partial` outcome. | 3 reviewers (independently reproduced: "$234 for a single $117 shipment") | `resolve_dispute` now records the `original_payer`; `_fold` carries it; `reconcile` treats a resolution as a **transfer** (relieve the original customer, charge the final payer), never a new charge. Demo now reports the true $631 total instead of an inflated $756. |
| C2 | **Service crashes under concurrent load.** Only `append()` held the store lock; every read (`get_by_key/for_parcel/all/count`) hit the shared sqlite connection unlocked. Under `serve.py`'s `ThreadingHTTPServer`, two concurrent GETs reproduced `Bus error`/`Segmentation fault`. | ledger reviewer (empirically reproduced twice) | `EventStore` now guards **every** connection access with a single re-entrant lock. |
| C3 | **`_emit` read-guard-write TOCTOU.** The FSM guard read wasn't atomic with the append, so two concurrent distinct commands on one parcel could both pass the guard and both persist — an illegal transition pair baked into the append-only log. | 4 reviewers | `Core._emit` now holds the store's re-entrant lock across the whole idempotency-check → guard → append sequence (nested store calls re-enter it). |
| C4 | **Safety gate defeated by a spelling variant.** `needs_human(["Hazmat"])` / `["high-value"]` returned `False`, so hazmat/customs/high-value/address-change parcels went fully autonomous. In production the flags come from an LLM/vision model, which will not emit canonical strings. | policy reviewer | `needs_human` normalizes each flag (case, hyphen, space) before intersecting the gate set. |

Tests grew **24 → 38**, all passing, including: netted `cover`, the previously-untested `decline`, human-gate
spelling variants, a pinned gate set, deterministic-replay equality, and a concurrent writers+readers guard.

## High — FIXED (follow-up pass)

| # | Finding | Fix |
|---|---------|-----|
| **H1** | **Policy and consent were not *enforced* in the core** — `core.py` never called `policy.py`, and `release`/`ship` trusted a caller-supplied `amount`, so nothing stopped a $500 charge on a parcel disclosed at $8, or a charge with no consent at all. The "no ambush" promise was a caller convention. | The core now **prices from policy itself** (`_price`) — `consent` records the policy amount and returns it; `release`/`ship` re-price and call `_assert_consent_covers`, which raises **before any write** if a non-zero customer charge lacks a prior consent for ≥ the amount and matching payer. `$0` amenity and dispute payers (hotel/porterly) are exempt. Demo section 8 now shows an unconsented $8 fee refused with 0 events written. |
| **M4a** | `resolve_dispute` raised a raw `KeyError` on an unknown outcome. | Now validated against an allow-list → clean `Rejected`. |
| **H3** | **Non-atomic two-event commands.** `disclose`/`release`/`ship`/`abandon` each emitted two events with a commit between them — a crash in the gap left `released`-without-`billed` (revenue leak) or `abandoned`-without-`closed` (stuck). | New `Core._emit_atomic` stages both events in **one transaction** (`store.append(commit=False)` ×N → `store.commit()`), rolling back on any failure. Each step is still FSM-guarded in order — the staged (uncommitted) row is visible to the next step's guard on the same connection, and the store's re-entrant lock keeps the batch a single isolated transaction (verified under 30-way concurrency: exact event counts, no bleed). |
| **H2** | **The core trusted the perception layer's self-report.** `apply_intent` gated on the proposer's own `match_confidence` and wrote the proposer's `fields` verbatim — so a forged `recipient_id`, a hallucinated confidence, or a tampered `master_account`/`kind` sailed straight into the `identified` event. Once the proposer is an LLM, that's trusting exactly the untrusted component the architecture exists to contain. | `apply_intent` → `_identify` now **corroborates against ground truth**: it re-runs the directory match against the observation recorded at *arrival* (`_observed_hint`), gates on the **core's own** confidence (never the proposer's), rejects a claimed `recipient_id` that contradicts the directory, and writes the directory's **authoritative** fields — the proposer's copy is discarded. Also fixes NIT #11: the core now acts only on a typed `ProposedIntent`. |

Tests after these passes: **54 passing**, adding: consent price is policy-authoritative, free pickup needs no
consent, unconsented paid charge refused (no partial write), charge-exceeding-consent refused, unknown outcome
rejected, and — for H3 — both events land on success, a crash mid-command rolls **both** back (no orphan event,
count unchanged), and a retry after the crash completes cleanly; and — for H2 — a forged recipient is refused
(and writes nothing), a hallucinated confidence on an ambiguous observation is refused, and tampered
`kind`/`master_account` fields are discarded in favor of the directory's truth.

**Tests: 61 passing.**

## Medium & remaining — FIXED (final pass)

| # | Finding | Fix |
|---|---------|-----|
| **H4** | Idempotency trusted the key, not the payload — a reused `op` with different data silently returned the stale event. | On a dedupe hit, `_emit`/`_emit_atomic` now compare stored vs incoming `data` and raise `Rejected("idempotency collision")` on mismatch. Identical retries still no-op. |
| **H5** | `serve.py` input hardening. | `Content-Length` parsed inside `try` (bad value → 400); body capped at 64 KB (→ 413); 15 s socket timeout (anti-slow-loris); non-object body → 400; `do_POST` now catches broadly (dict hint → 400, never a thread-killing 500); 500s no longer echo internals; and an **`Idempotency-Key` header** makes a retried POST a no-op (same parcel) — verified: retry → identical `parcel_id`, reused key + different data → 422. |
| **M1** | Silent mis-pricing. | `price_outbound` raises on an unknown mode/accessorial and on non-positive/`None` weight; `price_inbound` raises on an unknown choice; `auto_cover` rejects negatives. No pricing input is ever silently defaulted. |
| **M2** | `billed` bypassed the FSM guard. | Moved out of `NON_TRANSITIONING` into a new `GUARDED_ANNOTATION` — the money event is allowed only from `{released, shipped}`, still without changing state. |
| **M3** | Storage-twin blind spot. | `storage_map` now surfaces received-but-unshelved parcels (`arrived`/`captured`/`identified`) under a labelled `· intake — awaiting triage ·` bucket, so nothing physically in the building is invisible. |

**Tests: 74 passing.** Every finding from the review is now either fixed or a consciously-scoped note below.

## Fully resolved — nothing High/Medium outstanding

The original deferred tier (H1–H5, M1–M4a) is closed. What remains are conscious, documented scope notes, not defects:

These are real and worth doing, but they are **latent** (need a crash, concurrency, bad input, or a not-yet-built
endpoint to bite) or they change what the core *does* — a design decision to surface, not silently bundle under
"fix the criticals."

- **Auto-cover gating (H1 sliver).** The $40 auto-cover threshold is enforced by the caller, not gated inside
  `resolve_dispute`, because the core can't structurally tell "manager" from "auto-policy" by an actor string
  alone. Closing it properly needs an actor/role model — a deliberate future step, not a defect.
- **H2 production corroboration.** In production the proposer is a Claude vision call reading the photo; the core
  can't re-run that, so its corroboration is a structured cross-check (claimed recipient exists in the directory +
  the directory's known attributes match the intake label the core independently holds). The reference build's
  "re-match the arrival hint" is the faithful stand-in — same shape, same guarantee.
- **`"partial"` dispute outcome == `"cover"`** (full amount to hotel; no partial-split modelled yet).
- **`float` money throughout.** Fine for a reference build; a real billing statement should use `Decimal`/integer
  cents (rounding is applied at every step, so no wrong cent occurs in the current paths, but the representation is
  not audit-grade).
- **`parcel_id` derives from a 10-char slice of the arrival op** (`"PR-" + op[:10]`). Distinct ops sharing a
  10-char prefix would collide; a production build should use a full UUID or a caller-supplied id. (Out of scope
  for this pass — it lives in `arrive`, not the reviewed hardening surface.)

## Verified clean (checked, no defect)

Single write path (`store.append` called only from `core._emit`, grep-confirmed); no `UPDATE`/`DELETE` (append-only
is real); deterministic replay (`ORDER BY seq`, not `ts`); no SQL injection (parameter-bound; `find` is pure-Python);
`find("")` returns `[]` not everything; no import cycles; `auto_cover`/`confident`/`plausible_weight` boundaries
correct; idempotent-retry of the *same* command is genuinely safe under concurrency.
