"""Media indexer — full startup scan + incremental watchdog updates."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from portal import db
from portal.media_types import media_type as _media_type

log = logging.getLogger(__name__)

_SCAN_CONCURRENCY = 64   # max concurrent ffprobe OS subprocesses during startup scan (spread across cores by OS)
_WATCHER_CONCURRENCY = 24  # max concurrent ffprobe OS subprocesses for live watchdog events
_PROGRESS_INTERVAL = 30  # seconds between progress log lines during scan

_progress: dict = {"active": False, "total": 0, "done": 0}


def scan_progress() -> dict:
    p = _progress
    return {
        "active": p["active"],
        "total": p["total"],
        "done": p["done"],
        "remaining": max(0, p["total"] - p["done"]),
    }


async def _probe_file(path: Path) -> tuple[float | None, str | None]:
    """Run ffprobe to get duration and codec. Returns (None, None) on failure."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return None, None

        data = json.loads(stdout)
        duration = float(data.get("format", {}).get("duration", 0)) or None
        streams = data.get("streams", [])
        codec = next((s.get("codec_name") for s in streams if s.get("codec_type") in ("video", "audio")), None)
        return duration, codec
    except (asyncio.TimeoutError, FileNotFoundError, Exception):
        return None, None


async def _index_file(path: Path, db_path: Path) -> None:
    media_type = _media_type(path)
    if media_type is None:
        return

    stat = path.stat()

    # Skip if already indexed with the same mtime (file unchanged)
    existing = await db.get(db_path, str(path))
    if existing is not None and existing.mtime == stat.st_mtime:
        return

    duration, codec = await _probe_file(path)
    record = db.MediaRecord(
        path=str(path),
        media_type=media_type,
        size=stat.st_size,
        duration=duration,
        codec=codec,
        mtime=stat.st_mtime,
    )
    await db.upsert(db_path, record)


async def index_folder(files: list[Path], db_path: Path) -> None:
    """Index only the given files (those in a single viewed folder)."""
    sem = asyncio.Semaphore(8)

    async def _bounded(path: Path) -> None:
        async with sem:
            await _index_file(path, db_path)

    await asyncio.gather(*[_bounded(f) for f in files], return_exceptions=True)


async def full_scan(roots: list[Path], db_path: Path) -> None:
    _progress.update(active=True, total=0, done=0)

    async def _reporter() -> None:
        while _progress["active"]:
            await asyncio.sleep(_PROGRESS_INTERVAL)
            if _progress["active"]:
                p = _progress
                if p["total"] == 0:
                    log.info("Scan in progress: collecting file list…")
                else:
                    log.info("Scan progress: %d / %d complete (%d remaining)", p["done"], p["total"], p["total"] - p["done"])

    reporter = asyncio.create_task(_reporter())
    try:
        def _collect() -> list[Path]:
            return [
                Path(dirpath) / fname
                for root in roots
                for dirpath, _, filenames in os.walk(root)
                for fname in filenames
            ]

        all_files = await asyncio.to_thread(_collect)
        _progress["total"] = len(all_files)
        log.info("Scan started: %d files to index across %d roots", len(all_files), len(roots))

        sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

        async def _bounded(path: Path) -> None:
            async with sem:
                await _index_file(path, db_path)
            _progress["done"] += 1

        if all_files:
            await asyncio.gather(*[_bounded(f) for f in all_files], return_exceptions=True)
    finally:
        _progress["active"] = False
        reporter.cancel()

    log.info("Scan complete: %d / %d files indexed", _progress["done"], _progress["total"])


class _PortalEventHandler(FileSystemEventHandler):
    def __init__(self, db_path: Path, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._db_path = db_path
        self._loop = loop
        self._queue = queue

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ("created", event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ("modified", event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ("deleted", event.src_path))


async def _process_events(queue: asyncio.Queue, db_path: Path) -> None:
    # Debounce: track in-flight paths to skip duplicate inotify events (content + metadata writes)
    in_flight: set[str] = set()
    sem = asyncio.Semaphore(_WATCHER_CONCURRENCY)

    async def _handle(path_str: str) -> None:
        async with sem:
            try:
                await _index_file(Path(path_str), db_path)
            finally:
                in_flight.discard(path_str)

    while True:
        kind, path_str = await queue.get()
        if kind in ("created", "modified"):
            if path_str in in_flight:
                continue
            in_flight.add(path_str)
            asyncio.create_task(_handle(path_str))
        elif kind == "deleted":
            await db.remove(db_path, path_str)


async def start_watcher(roots: list[Path], db_path: Path) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    observer = Observer()
    for root in roots:
        handler = _PortalEventHandler(db_path, loop, queue)
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    log.info("Filesystem watcher started for %d roots", len(roots))

    asyncio.create_task(_process_events(queue, db_path))
