"""HTTP Range-aware media streaming."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from portal import allowlist, services

router = APIRouter()

_CHUNK = 1024 * 1024  # 1 MiB


@router.get("/stream")
async def stream(
    request: Request,
    path: str = Query(..., max_length=4096),
) -> Response:
    resolved = allowlist.resolve_and_check(path, services.roots())
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    file_size = resolved.stat().st_size
    media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    range_header = request.headers.get("Range")

    if range_header:
        return _range_response(resolved, range_header, file_size, media_type)

    return StreamingResponse(
        _iter_file(resolved, 0, file_size),
        media_type=media_type,
        headers={"Content-Length": str(file_size), "Accept-Ranges": "bytes"},
    )


def _range_response(path: Path, range_header: str, file_size: int, media_type: str) -> Response:
    start, end = _parse_range(range_header, file_size)
    length = end - start + 1
    return StreamingResponse(
        _iter_file(path, start, end + 1),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
        },
    )


def _parse_range(header: str, file_size: int) -> tuple[int, int]:
    try:
        unit, _, ranges = header.partition("=")
        if unit.strip() != "bytes":
            raise ValueError
        raw_start, _, raw_end = ranges.partition("-")
        start = int(raw_start) if raw_start else file_size - int(raw_end)
        end = int(raw_end) if raw_end else file_size - 1
        start = max(0, start)
        end = min(end, file_size - 1)
        return start, end
    except Exception:
        return 0, file_size - 1


async def _iter_file(path: Path, start: int, stop: int):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = stop - start
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
