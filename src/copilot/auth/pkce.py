"""PKCE helpers (RFC 7636).

The verifier is a random secret that never leaves this process. Zoho only sees its
SHA-256 hash (the challenge) at authorization time, then the verifier itself at token
exchange. So a stolen authorization code is useless without the verifier.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

ALLOWED_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
MIN_LEN = 43
MAX_LEN = 128
_ALLOWED = frozenset(ALLOWED_CHARS)


def _check_length(length: int) -> None:
    if not MIN_LEN <= length <= MAX_LEN:
        raise ValueError(f"PKCE verifier length must be {MIN_LEN}-{MAX_LEN}, got {length}")


def generate_verifier(length: int = 64) -> str:
    _check_length(length)
    return "".join(secrets.choice(ALLOWED_CHARS) for _ in range(length))


def challenge_s256(verifier: str) -> str:
    _check_length(len(verifier))
    if not set(verifier) <= _ALLOWED:
        raise ValueError("PKCE verifier contains characters outside the RFC 7636 unreserved set")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
