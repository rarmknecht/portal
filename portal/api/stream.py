"""HTTP Range-aware media streaming."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from portal import path_registry, services
from portal.media_types import media_type as _media_type

router = APIRouter()

_CHUNK = 1024 * 1024  # 1 MiB

# Never let a browser sniff a served file into something executable: a
# token can name any file inside a library, and libraries can contain
# things that aren't media.
_COMMON_HEADERS = {"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff"}


@router.get("/stream")
async def stream(
    request: Request,
    path: str = Query(..., max_length=4096),
) -> Response:
    resolved = path_registry.resolve_and_check(path, services.roots())
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Not a file")
    if _media_type(resolved) is None:
        # Browse lists everything in a library (unplayable files are shown
        # greyed out), but only media is ever served.
        raise HTTPException(status_code=415, detail="Not a media file")

    file_size = resolved.stat().st_size
    media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    range_header = request.headers.get("Range")

    if range_header:
        return _range_response(resolved, range_header, file_size, media_type)

    return StreamingResponse(
        _iter_file(resolved, 0, file_size),
        media_type=media_type,
        headers={"Content-Length": str(file_size), **_COMMON_HEADERS},
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
            **_COMMON_HEADERS,
        },
    )


def _unsatisfiable(file_size: int) -> HTTPException:
    return HTTPException(
        status_code=416,
        headers={"Content-Range": f"bytes */{file_size}"},
    )


def _parse_range(header: str, file_size: int) -> tuple[int, int]:
    """Parse a single-range ``Range`` header per RFC 9110 section 14.1.2.

    Raises HTTPException(416) for anything unsatisfiable or unparseable;
    multi-range requests (e.g. ``bytes=0-1,5-6``) are treated as
    unsatisfiable since this server only ever serves a single range.
    """
    if file_size == 0:
        raise _unsatisfiable(file_size)

    try:
        unit, sep, ranges = header.partition("=")
        if sep != "=" or unit.strip() != "bytes":
            raise ValueError("unsupported unit")
        if "," in ranges:
            raise ValueError("multiple ranges not supported")

        raw_start, dash, raw_end = ranges.partition("-")
        if not dash:
            raise ValueError("missing '-' in range")
        raw_start = raw_start.strip()
        raw_end = raw_end.strip()
        if raw_start == "" and raw_end == "":
            raise ValueError("empty range")

        if raw_start == "":
            # Suffix range: bytes=-N -> last N bytes. bytes=-0 is unsatisfiable.
            suffix_len = int(raw_end)
            if suffix_len <= 0:
                raise ValueError("non-positive suffix length")
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            start = int(raw_start)
            end = file_size - 1 if raw_end == "" else min(int(raw_end), file_size - 1)
    except Exception as exc:
        raise _unsatisfiable(file_size) from exc

    if start < 0 or start >= file_size or start > end:
        raise _unsatisfiable(file_size)

    return start, end


async def _iter_file(path: Path, start: int, stop: int):
    # aiofiles does the reads on a worker thread, so a slow disk or a
    # stalled network mount doesn't block every other request on the loop.
    async with aiofiles.open(path, "rb") as f:
        await f.seek(start)
        remaining = stop - start
        while remaining > 0:
            chunk = await f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
