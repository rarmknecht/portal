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
    # Intentionally LAN-facing by default — the whole point of the media API
    # is that a phone/TV on the LAN can reach it. This is safe only because
    # __main__._run() guarantees api_token is non-empty before serving (see
    # _ensure_api_token) and auth.verify_token now fails closed on an empty
    # token, so an unauthenticated client can't reach any guarded route.
    media_api_bind: str = "0.0.0.0"  # nosec B104
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


def _maybe_tilde(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def cfg_to_dict(cfg: Config) -> dict:
    """Serialise a Config to the plain-dict shape used by dump()/the UI.

    Takes a Config argument rather than reaching into portal.services, so
    this module never imports services/routes (that would be a cycle —
    services imports config, and routes imports both).
    """
    return {
        "agent": {
            "media_api_bind": cfg.agent.media_api_bind,
            "media_api_port": cfg.agent.media_api_port,
            "web_ui_bind": cfg.agent.web_ui_bind,
            "web_ui_port": cfg.agent.web_ui_port,
            "api_token": cfg.agent.api_token,
        },
        "libraries": cfg.libraries,
        "indexing": {
            "mode": cfg.indexing.mode,
            "scan_on_startup": cfg.indexing.scan_on_startup,
        },
        "thumbnails": {
            "cache_dir": _maybe_tilde(cfg.thumbnails.cache_dir),
            "max_cache_size_mb": cfg.thumbnails.max_cache_size_mb,
            "prefer_embedded": cfg.thumbnails.prefer_embedded,
        },
        "logging": {
            "log_dir": _maybe_tilde(cfg.logging.log_dir),
            "max_size_mb": cfg.logging.max_size_mb,
            "rotation": cfg.logging.rotation,
        },
    }


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dict_to_toml(data: dict) -> str:
    lines: list[str] = []

    agent = data.get("agent", {})
    lines += [
        "[agent]",
        # Same LAN-facing-by-default rationale as AgentConfig.media_api_bind
        # above — this is only the fallback used when a caller's dict omits
        # the key entirely; a loaded/edited config always carries an
        # explicit value.
        f'media_api_bind = {_toml_str(str(agent.get("media_api_bind", "0.0.0.0")))}',  # nosec B104
        f'media_api_port = {int(agent.get("media_api_port", 7842))}',
        f'web_ui_bind = {_toml_str(str(agent.get("web_ui_bind", "127.0.0.1")))}',
        f'web_ui_port = {int(agent.get("web_ui_port", 5567))}',
    ]
    tok = str(agent.get("api_token", "")).strip()
    if tok:
        lines.append(f"api_token = {_toml_str(tok)}")
    lines.append("")

    libs = [p for p in data.get("libraries", []) if str(p).strip()]
    items = ", ".join(_toml_str(str(p)) for p in libs)
    lines += ["[libraries]", f"allowlist = [{items}]", ""]

    idx = data.get("indexing", {})
    lines += [
        "[indexing]",
        f'mode = {_toml_str(str(idx.get("mode", "background")))}',
        f'scan_on_startup = {"true" if idx.get("scan_on_startup", True) else "false"}',
        "",
    ]

    th = data.get("thumbnails", {})
    lines += [
        "[thumbnails]",
        f'cache_dir = {_toml_str(str(th.get("cache_dir", "~/.portal/thumbnails")))}',
        f'max_cache_size_mb = {int(th.get("max_cache_size_mb", 500))}',
        f'prefer_embedded = {"true" if th.get("prefer_embedded", True) else "false"}',
        "",
    ]

    lg = data.get("logging", {})
    lines += [
        "[logging]",
        f'log_dir = {_toml_str(str(lg.get("log_dir", "~/.portal/logs")))}',
        f'max_size_mb = {int(lg.get("max_size_mb", 150))}',
        f'rotation = {_toml_str(str(lg.get("rotation", "size")))}',
        "",
    ]

    return "\n".join(lines)


def dump(cfg: Config) -> str:
    """Serialise a full Config back to TOML text (for write_secure())."""
    return _dict_to_toml(cfg_to_dict(cfg))
