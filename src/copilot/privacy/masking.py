"""PII masking for anything that could reach the LLM. >>> mask_users IS FOR YOU TO WRITE <<<

Principle: ALLOWLIST, not blocklist. A blocklist ("remove email, phone…") silently
leaks any new PII field Zoho adds later. An allowlist fails closed.

Spec
----
mask_users(users: list[dict]) -> list[dict]
    Input: the "users" array from GET /crm/v8/users (each item is a dict with keys such as
    id, full_name, email, phone, mobile, role, profile, status, confirm, ... ).
    Output: a NEW list (do not mutate the input) where each user becomes a dict with ONLY:
        "alias":   "user_1", "user_2", ... (1-based, in input order)
        "id":      copied as-is (ids are not PII and are needed to reference users in plans)
        "status":  copied if present
        "confirm": copied if present
        "role":    {"id": ..., "name": ...} only, if present (drop any other nested keys)
        "profile": {"id": ..., "name": ...} only, if present
    Every other key (full_name, email, phone, mobile, zuid, ...) is dropped.
    Keys that are missing in the input are simply absent in the output (no None values).

Tests: tests/test_masking.py
Run:   uv run pytest tests/test_masking.py
"""

from __future__ import annotations


def mask_users(users: list[dict]) -> list[dict]:
    raise NotImplementedError("for you to write, see the module docstring")


def pick(d: dict, keys: tuple[str, ...]) -> dict:
    """Keep only allowlisted keys that are present."""
    return {k: d[k] for k in keys if k in d}
