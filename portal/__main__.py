"""Portal agent entry point — starts media API + web UI servers."""

from __future__ import annotations

import asyncio
import logging
import sys
import uvicorn
from fastapi import FastAPI

from portal import auth, config as cfg_mod, db, discovery, indexer, services, thumbnailer
from portal.allowlist import allowlist_roots
from portal.api import health, libraries, browse, metadata, thumbnail, stream, search
from portal.ui import routes as ui_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("portal")


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
    app.include_router(ui_routes.router)
    return app


async def _run() -> None:
    cfg = cfg_mod.load()
    roots = allowlist_roots(cfg.libraries)

    if not roots and cfg.libraries:
        log.warning("No valid allowlist roots found — check your config paths")

    services.init(cfg, roots)

    for d in (cfg.data_dir, cfg.thumbnails.cache_dir, cfg.logging.log_dir):
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
