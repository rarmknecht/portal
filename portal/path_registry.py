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
    """Register *path* and return its opaque token. Idempotent for the same path.

    The path is canonicalized (symlinks and `..` resolved) before being
    stored, so a token can never map to the lexical path of a symlink that
    escapes its library root — every downstream consumer (browse, stream,
    thumbnail, metadata) operates on the resolved target only.
    """
    real = path.resolve()
    tok = hmac.new(_secret, str(real).encode(), hashlib.sha256).hexdigest()[:32]
    _registry[tok] = real
    return tok


def resolve_and_check(token: str, roots: list[Path]) -> Path:
    """Resolve *token* to a real path and verify it sits inside one of *roots*.

    Raises HTTP 400 for unknown tokens, 403 for allowlist violations, 404 if
    the file/directory no longer exists on disk.
    """
    path = _registry.get(token)
    if path is None:
        raise HTTPException(status_code=400, detail="Unknown path token")

    # Re-resolve at check time too: the stored path is already canonical
    # (token_for resolves before registering), but resolving again is cheap
    # and guards against any future caller that registers a raw path.
    real = path.resolve()

    for root in roots:
        try:
            real.relative_to(root.resolve())
            if not real.exists():
                raise HTTPException(status_code=404, detail="Not found")
            return real
        except ValueError:
            continue

    raise HTTPException(status_code=403, detail="Path outside allowlist")
