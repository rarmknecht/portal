from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from portal import allowlist, services
from portal.media_types import media_type

router = APIRouter()


def _entry(p: Path) -> dict:
    stat = p.stat()
    kind = "folder" if p.is_dir() else (media_type(p) or "other")
    return {
        "name": p.name,
        "path": str(p),
        "type": kind,
        "size": stat.st_size if p.is_file() else None,
        "mtime": stat.st_mtime,
    }


@router.get("/browse")
async def browse(path: str = Query(..., max_length=4096)) -> dict:
    resolved = allowlist.resolve_and_check(path, services.roots())
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    return {"path": str(resolved), "entries": [_entry(e) for e in entries]}
