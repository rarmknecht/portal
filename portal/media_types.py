"""Shared media type detection — extension sets and classifier."""

from __future__ import annotations

from pathlib import Path

VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"})
AUDIO_EXTS = frozenset({".mp3", ".aac", ".flac", ".m4a", ".ogg", ".wav"})
PHOTO_EXTS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".gif", ".bmp", ".webp"})


def media_type(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in PHOTO_EXTS:
        return "photo"
    return None
