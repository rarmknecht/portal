from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from portal import db, path_registry, services

router = APIRouter()


@router.get("/search")
async def search(
    library: str = Query(..., max_length=4096),
    q: str = Query(..., min_length=1, max_length=256),
) -> dict:
    roots = services.roots()
    matched_root = next((r for r in roots if str(r) == library or r.name == library), None)
    if matched_root is None:
        raise HTTPException(status_code=404, detail="Library not found")

    cfg = services.config()
    records = await db.search(cfg.db_path, str(matched_root), q)
    return {
        "query": q,
        "library": str(matched_root),
        "results": [
            {"path": path_registry.token_for(Path(r.path)), "name": Path(r.path).name, "type": r.media_type, "size": r.size}
            for r in records
        ],
    }
