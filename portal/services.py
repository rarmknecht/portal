"""Shared service singletons — config, allowlist roots, db path."""

from __future__ import annotations

from pathlib import Path

from portal.config import Config

_config: Config | None = None
_roots: list[Path] = []


def init(config: Config, roots: list[Path]) -> None:
    global _config, _roots
    _config = config
    _roots = roots


def config() -> Config:
    if _config is None:
        raise RuntimeError("services.init() not called")
    return _config


def roots() -> list[Path]:
    return _roots
