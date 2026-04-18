"""Thumbnail generation — extract embedded or generate via FFmpeg, cache to disk."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_FFMPEG_TIMEOUT = 10  # seconds
_ffmpeg_missing_logged = False


def _cache_key(media_path: Path, size: int) -> str:
    h = hashlib.sha1(str(media_path).encode()).hexdigest()
    return f"{h}_{size}.jpg"


async def get_thumbnail(
    media_path: Path,
    cache_dir: Path,
    size: int = 320,
    prefer_embedded: bool = True,
) -> bytes | None:
    cached = cache_dir / _cache_key(media_path, size)

    try:
        return cached.read_bytes()
    except FileNotFoundError:
        pass

    if prefer_embedded:
        data = await _run_ffmpeg(
            ["ffmpeg", "-y", "-i", str(media_path), "-an", "-vcodec", "copy", "-frames:v", "1", str(cached)],
            cached,
        )
        if data is not None:
            return data

    return await _run_ffmpeg(
        ["ffmpeg", "-y", "-ss", "00:00:05", "-i", str(media_path), "-frames:v", "1",
         "-vf", f"scale={size}:-1", "-f", "image2", str(cached)],
        cached,
    )


async def _run_ffmpeg(cmd: list[str], dest: Path) -> bytes | None:
    global _ffmpeg_missing_logged
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_TIMEOUT)
        if proc.returncode == 0:
            try:
                return dest.read_bytes()
            except FileNotFoundError:
                return None
    except asyncio.TimeoutError:
        dest.unlink(missing_ok=True)
    except FileNotFoundError:
        if not _ffmpeg_missing_logged:
            log.error("ffmpeg not found on PATH — thumbnails unavailable")
            _ffmpeg_missing_logged = True
        dest.unlink(missing_ok=True)
    return None


def ensure_cache_dir(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
