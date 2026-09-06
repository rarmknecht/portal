"""Configuration loading — TOML file with hardcoded defaults."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path.home() / ".portal"
_DEFAULT_CONFIG_PATH = _DEFAULT_DATA_DIR / "config.toml"


@dataclass
class AgentConfig:
    media_api_bind: str = "0.0.0.0"
    media_api_port: int = 7842
    web_ui_bind: str = "127.0.0.1"
    web_ui_port: int = 5567
    api_token: str = ""


@dataclass
class IndexingConfig:
    mode: str = "background"
    scan_on_startup: bool = True


@dataclass
class ThumbnailsConfig:
    cache_dir: Path = field(default_factory=lambda: _DEFAULT_DATA_DIR / "thumbnails")
    max_cache_size_mb: int = 500
    prefer_embedded: bool = True


@dataclass
class LoggingConfig:
    log_dir: Path = field(default_factory=lambda: _DEFAULT_DATA_DIR / "logs")
    max_size_mb: int = 150
    rotation: str = "size"


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    libraries: list[str] = field(default_factory=list)
    indexing: IndexingConfig = field(default_factory=IndexingConfig)
    thumbnails: ThumbnailsConfig = field(default_factory=ThumbnailsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    data_dir: Path = _DEFAULT_DATA_DIR

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.db"


def write_secure(path: Path, content: str) -> None:
    """Write `content` to `path` so only the owner can read it.

    The parent directory is created (or tightened) to 0700 and the file
    itself is written with 0600 permissions via a temp-file-then-rename
    so a reader never observes a partially written file, and a crash
    mid-write leaves the previous contents (if any) untouched.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if parent.stat().st_uid == os.getuid():
            os.chmod(parent, 0o700)
    except OSError:
        pass

    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=".config-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def secure_existing(path: Path) -> None:
    """Tighten permissions on an already-existing config file in place.

    Safe to call even when the file doesn't exist yet. Only acts on
    files/directories owned by the current user, and never raises —
    callers should still guard with try/except for anything unexpected
    from the filesystem (e.g. a read-only mount).
    """
    changed = False
    parent = path.parent
    if parent.exists() and parent.stat().st_uid == os.getuid():
        mode = parent.stat().st_mode & 0o777
        if mode != 0o700:
            os.chmod(parent, 0o700)
            changed = True

    if path.exists() and path.stat().st_uid == os.getuid():
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            os.chmod(path, 0o600)
            changed = True

    if changed:
        logger.info("Tightened permissions on %s to owner-only (0600/0700)", path)


def load(path: Path = _DEFAULT_CONFIG_PATH) -> Config:
    try:
        secure_existing(path)
    except OSError as exc:
        logger.warning("Could not tighten permissions on %s: %s", path, exc)

    cfg = Config()
    if not path.exists():
        return cfg

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    if agent := raw.get("agent"):
        cfg.agent = AgentConfig(
            media_api_bind=agent.get("media_api_bind", cfg.agent.media_api_bind),
            media_api_port=agent.get("media_api_port", cfg.agent.media_api_port),
            web_ui_bind=agent.get("web_ui_bind", cfg.agent.web_ui_bind),
            web_ui_port=agent.get("web_ui_port", cfg.agent.web_ui_port),
            api_token=agent.get("api_token", cfg.agent.api_token),
        )

    if libs := raw.get("libraries"):
        cfg.libraries = libs.get("allowlist", cfg.libraries)

    if idx := raw.get("indexing"):
        cfg.indexing = IndexingConfig(
            mode=idx.get("mode", cfg.indexing.mode),
            scan_on_startup=idx.get("scan_on_startup", cfg.indexing.scan_on_startup),
        )

    if thumbs := raw.get("thumbnails"):
        cache_dir = thumbs.get("cache_dir")
        cfg.thumbnails = ThumbnailsConfig(
            cache_dir=Path(cache_dir).expanduser() if cache_dir else cfg.thumbnails.cache_dir,
            max_cache_size_mb=thumbs.get("max_cache_size_mb", cfg.thumbnails.max_cache_size_mb),
            prefer_embedded=thumbs.get("prefer_embedded", cfg.thumbnails.prefer_embedded),
        )

    if log := raw.get("logging"):
        log_dir = log.get("log_dir")
        cfg.logging = LoggingConfig(
            log_dir=Path(log_dir).expanduser() if log_dir else cfg.logging.log_dir,
            max_size_mb=log.get("max_size_mb", cfg.logging.max_size_mb),
            rotation=log.get("rotation", cfg.logging.rotation),
        )

    return cfg
