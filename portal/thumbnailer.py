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

# Only these widths are ever rendered. Any requested size snaps up to the
# next one, so a client can't mint a fresh ffmpeg run and cache file for
# every integer between 64 and 1280.
SIZES = (160, 320, 640, 1280)

# Cap on ffmpeg processes running for thumbnails at once, across all
# requests. Anything beyond this waits its turn instead of forking.
_MAX_CONCURRENT = 4
_sem: asyncio.Semaphore | None = None

# Cache-size enforcement is a directory walk, so only do it every so many
# new thumbnails rather than after each one.
_EVICT_EVERY = 25
_writes_since_evict = 0


def snap_size(size: int) -> int:
    for s in SIZES:
        if size <= s:
            return s
    return SIZES[-1]


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_MAX_CONCURRENT)
    return _sem


def _cache_key(media_path: Path, size: int) -> str:
    # SHA-1 here only names a cache file, not a security control.
    # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    h = hashlib.sha1(str(media_path).encode(), usedforsecurity=False).hexdigest()
    return f"{h}_{size}.jpg"


def enforce_cache_limit(cache_dir: Path, max_bytes: int) -> int:
    """Delete the least recently used thumbnails until the cache is under
    *max_bytes* (with a little headroom so this doesn't run on every
    write). Returns the number of files removed. Blocking; call it from a
    worker thread."""
    if max_bytes <= 0:
        return 0
    entries: list[tuple[float, int, Path]] = []
    total = 0
    try:
        with os.scandir(cache_dir) as it:
            for e in it:
                if not e.is_file(follow_symlinks=False) or not e.name.endswith(".jpg"):
                    continue
                st = e.stat(follow_symlinks=False)
                entries.append((st.st_atime, st.st_size, Path(e.path)))
                total += st.st_size
    except OSError:
        return 0
    if total <= max_bytes:
        return 0
    target = int(max_bytes * 0.9)
    removed = 0
    for _, size, p in sorted(entries):
        if total <= target:
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
    if removed:
        log.info("Thumbnail cache over %d MiB; evicted %d files", max_bytes // (1024 * 1024), removed)
    return removed


async def get_thumbnail(
    media_path: Path,
    cache_dir: Path,
    size: int = 320,
    prefer_embedded: bool = True,
    max_cache_bytes: int = 0,
) -> bytes | None:
    global _writes_since_evict
    size = snap_size(size)
    cached = cache_dir / _cache_key(media_path, size)

    try:
        return cached.read_bytes()
    except FileNotFoundError:
        pass

    async with _semaphore():
        # Another request may have rendered it while we waited.
        try:
            return cached.read_bytes()
        except FileNotFoundError:
            pass

        data = None
        if prefer_embedded:
            data = await _run_ffmpeg(
                ["ffmpeg", "-y", "-i", str(media_path), "-an", "-vcodec", "copy", "-frames:v", "1", str(cached)],
                cached,
            )
        if data is None:
            data = await _run_ffmpeg(
                ["ffmpeg", "-y", "-ss", "00:00:05", "-i", str(media_path), "-frames:v", "1",
                 "-vf", f"scale={size}:-1", "-f", "image2", str(cached)],
                cached,
            )

    if data is not None and max_cache_bytes > 0:
        _writes_since_evict += 1
        if _writes_since_evict >= _EVICT_EVERY:
            _writes_since_evict = 0
            await asyncio.to_thread(enforce_cache_limit, cache_dir, max_cache_bytes)
    return data


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
                except (ProcessLookupError, PermissionError):
                    pass  # reap() runs in a finally; never let it mask the caller's result
        else:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
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
            stdin=asyncio.subprocess.DEVNULL,
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
