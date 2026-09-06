"""Portal agent entry point — starts media API + web UI servers."""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from portal import auth, config as cfg_mod, db, discovery, indexer, services, thumbnailer
from portal.allowlist import allowlist_roots
from portal.api import health, libraries, browse, metadata, thumbnail, stream, search
from portal.ui import routes as ui_routes
from portal.ui.security import LoopbackOnlyMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("portal")


def _require_loopback_bind(value: str) -> None:
    """Refuse to start the web UI on anything but a loopback address.

    ``LoopbackOnlyMiddleware`` (portal/ui/security.py) trusts the UI
    server's own peer address as its primary check — that only means
    anything if the server is actually bound to loopback. The UI has no
    authentication and exposes the API token via ``GET /api/config``, so a
    non-loopback ``web_ui_bind`` (e.g. from a hand-edited config or the
    settings page) would hand out unauthenticated access to any remote
    client. Bail out before any server starts rather than serve that.
    """
    if not cfg_mod.is_loopback_bind(value):
        log.error(
            "web_ui_bind=%r is not a loopback address — "
            "set web_ui_bind to 127.0.0.1 in ~/.portal/config.toml",
            value,
        )
        sys.exit(2)


def _ensure_api_token(cfg: cfg_mod.Config, path: Path) -> bool:
    """Generate and persist an api_token if the loaded config doesn't have one.

    The media API defaults to binding 0.0.0.0 (see AgentConfig.media_api_bind)
    because that's the point of the product — a phone on the LAN should be
    able to reach it. That's only safe because auth.verify_token now fails
    closed on an empty token, which in turn requires that a fresh install
    always ends up with a real token before any server starts serving.

    Mutates cfg.agent.api_token in place and writes the full config back to
    `path` via config.write_secure() (0600/0700, atomic) so the token
    survives restarts and is visible in the web UI. Returns True if a token
    was generated (and the file written), False if cfg already had one (in
    which case the file is left untouched).
    """
    if cfg.agent.api_token:
        return False
    cfg.agent.api_token = secrets.token_urlsafe(32)
    cfg_mod.write_secure(path, cfg_mod.dump(cfg))
    return True


def _build_media_app() -> FastAPI:
    from fastapi import Depends
    app = FastAPI(title="Portal Media API", version="1.0")
    prefix = "/api/v1"
    guarded = {"dependencies": [Depends(auth.verify_token)]}
    app.include_router(health.router, prefix=prefix)  # health exempt — used for connectivity probing
    app.include_router(libraries.router, prefix=prefix, **guarded)
    app.include_router(browse.router, prefix=prefix, **guarded)
    app.include_router(metadata.router, prefix=prefix, **guarded)
    app.include_router(thumbnail.router, prefix=prefix, **guarded)
    app.include_router(stream.router, prefix=prefix, **guarded)
    app.include_router(search.router, prefix=prefix, **guarded)
    return app


def _build_ui_app() -> FastAPI:
    app = FastAPI(title="Portal Web UI", version="1.0")
    # The UI has no auth of its own (see portal/ui/security.py) — it relies
    # entirely on being loopback-only. This middleware enforces that on
    # every request, since app.include_router() alone applies no such check.
    app.add_middleware(
        LoopbackOnlyMiddleware,
        get_port=lambda: services.config().agent.web_ui_port,
    )
    app.include_router(ui_routes.router)
    return app


async def _run() -> None:
    cfg = cfg_mod.load()
    _require_loopback_bind(cfg.agent.web_ui_bind)

    config_path = cfg_mod.DEFAULT_CONFIG_PATH
    if _ensure_api_token(cfg, config_path):
        log.info(
            "No api_token was configured; generated one and saved it to %s. "
            "View it in the web UI at http://127.0.0.1:%d/",
            config_path,
            cfg.agent.web_ui_port,
        )

    # Belt and braces: _ensure_api_token() above should make this
    # unreachable, but if cfg.agent.api_token is somehow still empty while
    # the media API is bound off-loopback, refuse to serve an unauthenticated
    # LAN-facing API rather than silently trust that invariant.
    if not cfg_mod.is_loopback_bind(cfg.agent.media_api_bind) and not cfg.agent.api_token:
        log.error(
            "media_api_bind=%r is not loopback and no api_token is set — "
            "refusing to serve the media API without authentication",
            cfg.agent.media_api_bind,
        )
        sys.exit(2)

    roots = allowlist_roots(cfg.libraries)

    if not roots and cfg.libraries:
        log.warning("No valid allowlist roots found — check your config paths")

    services.init(cfg, roots)

    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for d in (cfg.thumbnails.cache_dir, cfg.logging.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    thumbnailer.ensure_cache_dir(cfg.thumbnails.cache_dir)

    await db.init(cfg.db_path)

    if roots:
        await indexer.start_watcher(roots, cfg.db_path)

    await discovery.advertise(cfg.agent.media_api_port)

    media_cfg = uvicorn.Config(
        _build_media_app(),
        host=cfg.agent.media_api_bind,
        port=cfg.agent.media_api_port,
        log_level="warning",
    )
    ui_cfg = uvicorn.Config(
        _build_ui_app(),
        host=cfg.agent.web_ui_bind,
        port=cfg.agent.web_ui_port,
        log_level="warning",
        # LoopbackOnlyMiddleware's peer check must see the real socket peer
        # (scope["client"]) — uvicorn's proxy_headers support (on by default)
        # would let a client-supplied X-Forwarded-For rewrite that address,
        # letting a remote caller impersonate a loopback peer.
        proxy_headers=False,
    )

    media_server = uvicorn.Server(media_cfg)
    ui_server = uvicorn.Server(ui_cfg)

    log.info(
        "Portal agent started — media API on %s:%d, web UI on %s:%d",
        cfg.agent.media_api_bind,
        cfg.agent.media_api_port,
        cfg.agent.web_ui_bind,
        cfg.agent.web_ui_port,
    )

    try:
        await asyncio.gather(media_server.serve(), ui_server.serve())
    finally:
        await discovery.deregister()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Portal agent stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
