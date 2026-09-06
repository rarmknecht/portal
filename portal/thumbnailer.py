"""Thumbnail generation — extract embedded or generate via FFmpeg, cache to disk."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
from pathlib import Path

log = logging.getLogger(__name__)

_FFMPEG_TIMEOUT = 10  # seconds
_ffmpeg_missing_logged = False


def _cache_key(media_path: Path, size: int) -> str:
    # SHA-1 here only names a cache file, not a security control.
    # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    h = hashlib.sha1(str(media_path).encode(), usedforsecurity=False).hexdigest()
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


async def reap(proc: asyncio.subprocess.Process) -> None:
    """Kill *proc* if it's still running and reap it, so a timed-out or
    cancelled child never lingers as an orphan/zombie.

    The process is spawned with start_new_session=True, making it the
    leader of its own process group (pid == pgid), so killing the whole
    group with os.killpg also takes out any children it forked itself
    (some distro/flatpak/nix ffprobe/ffmpeg shims are `sh` wrappers that
    fork a real grandchild instead of exec'ing into it) — plain proc.kill()
    only signals the direct child and leaves such a grandchild orphaned and
    running. Fall back to proc.kill() if the group kill can't be done.

    With stdout/stderr=PIPE, asyncio's subprocess transport also only
    resolves proc.wait() once its pipe transports have disconnected, and an
    orphaned grandchild inheriting the pipe's write end would otherwise
    hold that open — so close the pipe transports ourselves right after
    killing, and bound the final wait as a belt-and-braces guard.
    """
    if proc.returncode is None:
        if proc.pid is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            for fd in (1, 2):
                pipe = transport.get_pipe_transport(fd)
                if pipe is not None:
                    pipe.close()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            log.warning("Timed out waiting to reap killed process pid=%s", getattr(proc, "pid", "?"))


async def _run_ffmpeg(cmd: list[str], dest: Path) -> bytes | None:
    global _ffmpeg_missing_logged
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
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
    finally:
        if proc is not None:
            await reap(proc)
    return None


def ensure_cache_dir(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
