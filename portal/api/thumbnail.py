from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from portal import path_registry, services, thumbnailer
from portal.media_types import media_type as _media_type

router = APIRouter()


@router.get("/thumbnail")
async def thumbnail(
    path: str = Query(..., max_length=4096),
    size: int = Query(default=320, ge=64, le=1280),
) -> Response:
    resolved = path_registry.resolve_and_check(path, services.roots())
    if not resolved.is_file() or _media_type(resolved) is None:
        # ffmpeg only ever runs on things we'd also be willing to stream.
        raise HTTPException(status_code=415, detail="Not a media file")
    cfg = services.config()

    data = await thumbnailer.get_thumbnail(
        resolved,
        cache_dir=cfg.thumbnails.cache_dir,
        size=thumbnailer.snap_size(size),
        prefer_embedded=cfg.thumbnails.prefer_embedded,
        max_cache_bytes=cfg.thumbnails.max_cache_size_mb * 1024 * 1024,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Thumbnail unavailable")

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff"},
    )
