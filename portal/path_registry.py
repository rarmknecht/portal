"""Opaque path token registry — maps short HMAC tokens to real filesystem paths."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

from fastapi import HTTPException

_secret: bytes = secrets.token_bytes(32)
_registry: dict[str, Path] = {}


def token_for(path: Path) -> str:
    """Register *path* and return its opaque token. Idempotent for the same path."""
    tok = hmac.new(_secret, str(path).encode(), hashlib.sha256).hexdigest()[:32]
    _registry[tok] = path
    return tok


def resolve_and_check(token: str, roots: list[Path]) -> Path:
    """Resolve *token* to a real path and verify it sits inside one of *roots*.

    Raises HTTP 400 for unknown tokens, 403 for allowlist violations, 404 if
    the file/directory no longer exists on disk.
    """
    path = _registry.get(token)
    if path is None:
        raise HTTPException(status_code=400, detail="Unknown path token")

    for root in roots:
        try:
            path.relative_to(root)
            if not path.exists():
                raise HTTPException(status_code=404, detail="Not found")
            return path
        except ValueError:
            continue

    raise HTTPException(status_code=403, detail="Path outside allowlist")
