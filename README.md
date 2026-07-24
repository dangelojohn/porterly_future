# Porterly — reference build (the runnable spine)

A working, dependency-free implementation of the AI-native architecture in the
companion artifacts. It's not the full product — it's the **spine**, built so the
core ideas are executable and testable, not just diagrams.

> **The one rule:** *perception is probabilistic, actuation is deterministic.*
> An agent may read and **propose** a structured intent; only the typed, guarded,
> idempotent **Core** may change state / money / shipping.

## Quick start (Python 3.9+, no pip install)

```bash
cd reference-build
python3 tests.py     # 74 invariant tests — the "invalid states made impossible"
python3 demo.py      # end-to-end walkthrough of plausible packages
python3 serve.py     # a tiny HTTP surface: curl http://127.0.0.1:8787/health
```

A five-reviewer deep code review (2026-07-23) hardened this build — see `CODE_REVIEW.md`. Four
critical findings were fixed (reconcile double-count, thread-safety crash, `_emit` TOCTOU, a
safety-gate spelling bypass), then the **entire remaining tier was closed** too:
**policy + consent enforced by the core** (H1 — pricing computed from policy, no customer billed beyond a prior
consent), **two-event commands atomic** (H3 — all-or-nothing), **identity corroborated against ground truth**
(H2 — the core re-derives the recipient from the trusted directory, refusing forged/hallucinated/tampered
proposals), **payload-aware idempotency** (H4), **HTTP hardening + idempotency key** (H5), **pricing that refuses
unknown inputs** (M1), **a guarded `billed` money event** (M2), and **a storage twin with no blind spot** (M3).
The core now trusts neither a caller's *money* nor a perceiver's *identity claim*, and every finding from the
five-reviewer audit is fixed.

## What it proves (and where it maps to the design)

| Idea in the artifacts | Where it lives here | Shown by |
|---|---|---|
| Event-sourced ledger, idempotent | `porterly/ledger.py` | retried command writes **once** |
| State is a guarded FSM, not a mutable field | `porterly/fsm.py` | `ship-before-store` **blocked**; nothing written |
| Perception → **corroborate** → execute | `porterly/core.py` `apply_intent`/`_identify` | ambiguous/hazmat proposals **rejected** |
| Core re-derives identity from ground truth | `core._identify` | forged recipient / hallucinated confidence / tampered fields **refused** |
| Agents propose-only (LLM seam) | `porterly/agents.py` | `propose_identity` returns an intent, never acts |
| Policy-as-data, **enforced in the core** | `core._price` / `policy.py` | caller can't set a price; core computes it from policy |
| No ambush: consent covers every charge | `core._assert_consent_covers` | a paid charge with no matching consent is **refused** |
| Two-event commands are atomic | `core._emit_atomic` | a crash mid-command rolls **both** events back — no orphan |
| Guest vs exhibitor at capture | `policy.segment` | guest mail is **free**; exhibitor freight priced |
| Projections, replayable | `porterly/projections.py` | state is a **fold** of events |
| Find-a-package + custody chain | `projections.find / custody_chain` | search by name/tracking → immutable trail |
| Lost/stolen investigation | demo §6 | drift → theft → lost, chain ends at time+place+person |
| Reconcile replaces the meeting | `projections.reconcile` | settled statement: paid / covered / absorbed |

## Module map

```
porterly/
  ledger.py       append-only event store (SQLite), idempotent dedupe_key
  fsm.py          guarded parcel state machine (raises on illegal transition)
  policy.py       policy-as-data: pricing, segmentation, auto-cover thresholds
  contacts.py     recipient directory + a confidence-returning matcher
  validators.py   semantic checks + the human-gate list
  agents.py       ProposedIntent + the propose-only perception seam (LLM goes here)
  core.py         the deterministic decider — the ONLY thing that mutates
  projections.py  read models: state, storage twin, find, custody, reconcile
demo.py           end-to-end scenarios (guest, exhibitor, freight, lost/stolen, disputes)
tests.py          24 invariant assertions
serve.py          stdlib HTTP surface (health/parcels/find/storage/reconcile/inbound)
```

## Stubbed here → production (per the Tech-Stack build sheet)

| Reference build | Production |
|---|---|
| SQLite, in-process | **Managed Postgres** (events + projections), PITR |
| Deterministic matcher | **Claude** perception (`agents.propose_identity` is the seam) |
| In-proc side-effects | **Outbox + workers**; **Stripe / EasyPost / Twilio** adapters |
| Accounting = none | **QuickBooks Online** sink (bank / payroll / tax) |
| `serve.py` stdlib | **FastAPI**; the three surfaces (dock PWA, guest chat, console) |

The seams are deliberate: swapping the deterministic matcher for a Claude call,
or SQLite for Postgres, changes nothing downstream — the Core still validates and
the Ledger still records. That decoupling is the whole point.

## Companion artifacts
Vision · Blueprint · Screens · Tech Stack · Process Map · Console — in
`../` (the *Porterly Future* folder) and published on claude.ai.
