import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from portal import indexer, path_registry, services
from portal.media_types import media_type

log = logging.getLogger(__name__)
router = APIRouter()


def _entry(p: Path) -> dict:
    stat = p.stat()
    kind = "folder" if p.is_dir() else (media_type(p) or "other")
    return {
        "name": p.name,
        "path": path_registry.token_for(p),
        "type": kind,
        "size": stat.st_size if p.is_file() else None,
        "mtime": stat.st_mtime,
    }


@router.get("/browse")
async def browse(path: str = Query(..., max_length=4096)) -> dict:
    resolved = path_registry.resolve_and_check(path, services.roots())
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

    media_files = [e for e in entries if e.is_file() and media_type(e) is not None]
    if media_files:
        db_path = services.config().db_path
        t = asyncio.create_task(indexer.index_folder(media_files, db_path))
        t.add_done_callback(lambda f: f.exception() and log.error("index_folder failed: %s", f.exception()))

    return {"path": path, "entries": [_entry(e) for e in entries]}
