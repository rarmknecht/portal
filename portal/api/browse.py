import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from portal import indexer, path_registry, services
from portal.allowlist import within_any
from portal.media_types import media_type

log = logging.getLogger(__name__)
router = APIRouter()


def _entry(p: Path) -> dict | None:
    """Build a listing entry for *p*. Returns None (drop the entry) rather
    than letting a stat() failure on one bad entry (e.g. a symlink that was
    deleted out from under us between listing and stat, or one that slipped
    past filtering) 500 the whole /browse response."""
    try:
        stat = p.stat()
        is_dir = p.is_dir()
        is_file = p.is_file()
        kind = "folder" if is_dir else (media_type(p) or "other")
        return {
            "name": p.name,
            "path": path_registry.token_for(p),
            "type": kind,
            "size": stat.st_size if is_file else None,
            "mtime": stat.st_mtime,
        }
    except OSError:
        return None


def _safe_entries(directory: Path, roots: list[Path]) -> list[Path]:
    """List *directory*, dropping any entry whose resolved target (following
    symlinks) escapes the allowlisted roots. A symlink inside a library that
    points outside of it must never get a token or show up in a listing.

    Also drops dangling symlinks and symlink loops: Path.resolve() defaults
    to strict=False and does not raise for either, so entry.exists() (which
    follows symlinks and swallows OSError, including ELOOP, returning False)
    is checked first — resolving a loop can otherwise hang/loop internally
    and stat-ing a dangling link always raises further down the pipeline.
    """
    resolved_roots = [r.resolve() for r in roots]
    safe = []
    for entry in directory.iterdir():
        if not entry.exists():
            continue
        try:
            real = entry.resolve()
        except OSError:
            continue
        if not within_any(real, resolved_roots):
            continue
        safe.append(entry)
    return safe


@router.get("/browse")
async def browse(path: str = Query(..., max_length=4096)) -> dict:
    resolved = path_registry.resolve_and_check(path, services.roots())
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = sorted(_safe_entries(resolved, services.roots()), key=lambda p: (not p.is_dir(), p.name.lower()))

    # Index under the *resolved* path so the DB key matches the token
    # (token_for() resolves too) — otherwise /metadata looks up an in-root
    # symlink's real target by token but the DB row was keyed by the link.
    media_files = []
    for e in entries:
        if not e.is_file() or media_type(e) is None:
            continue
        try:
            media_files.append(e.resolve())
        except OSError:
            continue

    if media_files:
        db_path = services.config().db_path
        t = asyncio.create_task(indexer.index_folder(media_files, db_path))
        t.add_done_callback(lambda f: f.exception() and log.error("index_folder failed: %s", f.exception()))

    results = [entry for e in entries if (entry := _entry(e)) is not None]
    return {"path": path, "entries": results}
