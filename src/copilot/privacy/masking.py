"""PII masking for anything that could reach the LLM.

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

_USER_KEYS = ("id", "status", "confirm")
_REF_KEYS = ("id", "name")


def mask_users(users: list[dict]) -> list[dict]:
    masked = []
    for n, user in enumerate(users, start=1):
        out = {"alias": f"user_{n}", **pick(user, _USER_KEYS)}
        for ref in ("role", "profile"):
            if isinstance(user.get(ref), dict):
                out[ref] = pick(user[ref], _REF_KEYS)
        masked.append(out)
    return masked


def pick(d: dict, keys: tuple[str, ...]) -> dict:
    """Keep only allowlisted keys that are present."""
    return {k: d[k] for k in keys if k in d}
