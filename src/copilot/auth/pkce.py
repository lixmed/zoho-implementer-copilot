"""PKCE helpers (RFC 7636). >>> FOR YOU TO WRITE <<<

Spec
----
generate_verifier(length: int = 64) -> str
    - Return a cryptographically random code verifier of exactly `length` characters.
    - Allowed characters: A-Z a-z 0-9 and "-" "." "_" "~"  (RFC 7636 "unreserved").
    - `length` must be between 43 and 128 inclusive, otherwise raise ValueError.
    - Use the `secrets` module (never `random`).

challenge_s256(verifier: str) -> str
    - Return BASE64URL( SHA256( ASCII(verifier) ) ) with the trailing "=" padding removed.
    - Raise ValueError if the verifier has an invalid length or contains a character
      outside the allowed set.

Tests: tests/test_pkce.py (includes the official RFC 7636 Appendix B test vector).
Run:   uv run pytest tests/test_pkce.py
"""

from __future__ import annotations

ALLOWED_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
MIN_LEN = 43
MAX_LEN = 128


def generate_verifier(length: int = 64) -> str:
    raise NotImplementedError("for you to write, see the module docstring")


def challenge_s256(verifier: str) -> str:
    raise NotImplementedError("for you to write, see the module docstring")
