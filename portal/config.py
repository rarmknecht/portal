"""Configuration loading — TOML file with hardcoded defaults."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

_DEFAULT_DATA_DIR = Path.home() / ".portal"
_DEFAULT_CONFIG_PATH = _DEFAULT_DATA_DIR / "config.toml"


@dataclass
class AgentConfig:
    media_api_bind: str = "0.0.0.0"
    media_api_port: int = 7842
    web_ui_bind: str = "127.0.0.1"
    web_ui_port: int = 5567


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


def load(path: Path = _DEFAULT_CONFIG_PATH) -> Config:
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
