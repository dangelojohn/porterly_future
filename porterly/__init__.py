"""Porterly — AI-native reference implementation (the spine).

Perception is probabilistic, actuation is deterministic:
  * agents PROPOSE a structured intent,
  * the deterministic Core VALIDATES it (semantic checks + FSM guard + human gates),
  * only the Core APPENDS events to the idempotent, append-only Ledger.

This package is the runnable proof of the architecture in the companion artifacts.
Pure standard-library so it runs with zero setup. SQLite stands in for managed
Postgres; a deterministic matcher stands in for the Claude perception step.
"""
__all__ = ["ledger", "fsm", "policy", "contacts", "validators", "agents", "core", "projections"]
