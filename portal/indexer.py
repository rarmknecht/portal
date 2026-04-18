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

_SCAN_CONCURRENCY = 64  # max concurrent ffprobe + db writes during startup scan


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


async def full_scan(roots: list[Path], db_path: Path) -> None:
    log.info("Starting full index scan of %d roots", len(roots))
    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def _bounded(path: Path) -> None:
        async with sem:
            await _index_file(path, db_path)

    tasks = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                tasks.append(_bounded(Path(dirpath) / fname))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Full scan complete")


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
    while True:
        kind, path_str = await queue.get()
        if kind in ("created", "modified"):
            if path_str in in_flight:
                continue
            in_flight.add(path_str)
            try:
                await _index_file(Path(path_str), db_path)
            finally:
                in_flight.discard(path_str)
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
