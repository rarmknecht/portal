from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from portal import allowlist, services, thumbnailer

router = APIRouter()


@router.get("/thumbnail")
async def thumbnail(
    path: str = Query(..., max_length=4096),
    size: int = Query(default=320, ge=64, le=1280),
) -> Response:
    resolved = allowlist.resolve_and_check(path, services.roots())
    cfg = services.config()

    data = await thumbnailer.get_thumbnail(
        resolved,
        cache_dir=cfg.thumbnails.cache_dir,
        size=size,
        prefer_embedded=cfg.thumbnails.prefer_embedded,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Thumbnail unavailable")

    return Response(content=data, media_type="image/jpeg")
