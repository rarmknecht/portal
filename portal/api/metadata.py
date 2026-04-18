from fastapi import APIRouter, Query

from portal import allowlist, db, services

router = APIRouter()


@router.get("/metadata")
async def metadata(path: str = Query(..., max_length=4096)) -> dict:
    resolved = allowlist.resolve_and_check(path, services.roots())
    cfg = services.config()
    record = await db.get(cfg.db_path, str(resolved))

    stat = resolved.stat()
    return {
        "path": str(resolved),
        "name": resolved.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "type": record.media_type if record else "unknown",
        "duration": record.duration if record else None,
        "codec": record.codec if record else None,
    }
