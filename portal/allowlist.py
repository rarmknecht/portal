"""Allowlist path enforcement — canonicalize and validate every path parameter."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


def resolve_and_check(raw_path: str, roots: list[Path]) -> Path:
    """Resolve *raw_path* and confirm it sits inside one of *roots*.

    Raises HTTP 400 for empty/missing paths, HTTP 403 for traversal or
    symlink-escape attempts, HTTP 404 if the resolved path doesn't exist.
    """
    if not raw_path or len(raw_path) > 4096:
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        resolved = Path(raw_path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not _within_any(resolved, roots):
        raise HTTPException(status_code=403, detail="Path outside allowlist")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Not found")

    return resolved


def _within_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def allowlist_roots(paths: list[str]) -> list[Path]:
    """Resolve and return only existing allowlist roots."""
    result = []
    for p in paths:
        resolved = Path(p).expanduser().resolve()
        if resolved.is_dir():
            result.append(resolved)
    return result
