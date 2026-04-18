"""SQLite index — schema init, WAL mode, media record model."""

from __future__ import annotations

import aiosqlite
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaRecord:
    path: str
    media_type: str       # "video" | "audio" | "photo"
    size: int
    duration: float | None
    codec: str | None
    mtime: float


_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS media (
    path      TEXT PRIMARY KEY,
    type      TEXT NOT NULL,
    size      INTEGER NOT NULL,
    duration  REAL,
    codec     TEXT,
    mtime     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_type ON media(type);
"""


async def init(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_DDL)
        await db.commit()


async def upsert(db_path: Path, record: MediaRecord) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO media (path, type, size, duration, codec, mtime)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                type=excluded.type, size=excluded.size,
                duration=excluded.duration, codec=excluded.codec,
                mtime=excluded.mtime
            """,
            (record.path, record.media_type, record.size, record.duration, record.codec, record.mtime),
        )
        await db.commit()


async def remove(db_path: Path, path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM media WHERE path = ?", (path,))
        await db.commit()


async def get(db_path: Path, path: str) -> MediaRecord | None:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT path, type, size, duration, codec, mtime FROM media WHERE path = ?", (path,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return MediaRecord(path=row[0], media_type=row[1], size=row[2], duration=row[3], codec=row[4], mtime=row[5])


async def search(db_path: Path, library_root: str, query: str, limit: int = 200) -> list[MediaRecord]:
    pattern = f"%{query}%"
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            SELECT path, type, size, duration, codec, mtime FROM media
            WHERE path LIKE ? AND path LIKE ?
            ORDER BY path LIMIT ?
            """,
            (f"{library_root}%", pattern, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [MediaRecord(path=r[0], media_type=r[1], size=r[2], duration=r[3], codec=r[4], mtime=r[5]) for r in rows]
