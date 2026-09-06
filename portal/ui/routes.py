"""Web UI routes — served on 127.0.0.1:5567."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from portal import config as cfg_mod, indexer, services

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

router = APIRouter()
_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()
_CONFIG_PATH = Path.home() / ".portal" / "config.toml"

# The TOML (de)serialisation helpers used to live here; they moved to
# portal/config.py (as cfg_to_dict()/_dict_to_toml()/dump()) so __main__.py
# can persist a config without importing this UI-routes module. Re-exported
# here so existing call sites/tests keep working unchanged.
_maybe_tilde = cfg_mod._maybe_tilde
_toml_str = cfg_mod._toml_str
_dict_to_toml = cfg_mod._dict_to_toml


def _cfg_to_dict() -> dict:
    return cfg_mod.cfg_to_dict(services.config())


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)


@router.get("/api/status")
async def status() -> JSONResponse:
    cfg = services.config()
    return JSONResponse({
        "status": "running",
        "media_api_port": cfg.agent.media_api_port,
        "web_ui_port": cfg.agent.web_ui_port,
        "libraries": [{"name": r.name, "path": str(r)} for r in services.roots()],
        "scan": indexer.scan_progress(),
    })


@router.get("/api/config")
async def get_config() -> JSONResponse:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            raw = tomllib.load(f)
        cfg = services.config()
        agent_r = raw.get("agent", {})
        libs_r = raw.get("libraries", {})
        idx_r = raw.get("indexing", {})
        th_r = raw.get("thumbnails", {})
        lg_r = raw.get("logging", {})
        return JSONResponse({
            "agent": {
                "media_api_bind": agent_r.get("media_api_bind", cfg.agent.media_api_bind),
                "media_api_port": agent_r.get("media_api_port", cfg.agent.media_api_port),
                "web_ui_bind": agent_r.get("web_ui_bind", cfg.agent.web_ui_bind),
                "web_ui_port": agent_r.get("web_ui_port", cfg.agent.web_ui_port),
                "api_token": agent_r.get("api_token", cfg.agent.api_token),
            },
            "libraries": libs_r.get("allowlist", cfg.libraries),
            "indexing": {
                "mode": idx_r.get("mode", cfg.indexing.mode),
                "scan_on_startup": idx_r.get("scan_on_startup", cfg.indexing.scan_on_startup),
            },
            "thumbnails": {
                "cache_dir": th_r.get("cache_dir", _maybe_tilde(cfg.thumbnails.cache_dir)),
                "max_cache_size_mb": th_r.get("max_cache_size_mb", cfg.thumbnails.max_cache_size_mb),
                "prefer_embedded": th_r.get("prefer_embedded", cfg.thumbnails.prefer_embedded),
            },
            "logging": {
                "log_dir": lg_r.get("log_dir", _maybe_tilde(cfg.logging.log_dir)),
                "max_size_mb": lg_r.get("max_size_mb", cfg.logging.max_size_mb),
                "rotation": lg_r.get("rotation", cfg.logging.rotation),
            },
        })
    return JSONResponse(_cfg_to_dict())


@router.post("/api/config")
async def save_config(request: Request) -> JSONResponse:
    # Require an explicit JSON content type so a cross-site
    # enctype="text/plain" form post — the classic no-CORS-preflight CSRF
    # trick — can't reach request.json() even if it slipped past the
    # LoopbackOnlyMiddleware Origin/Host checks. (See portal/ui/security.py
    # for the rest of the loopback-only guard.)
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    body = await request.json()
    toml_content = _dict_to_toml(body)
    try:
        tomllib.loads(toml_content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Config validation failed: {exc}")
    cfg_mod.write_secure(_CONFIG_PATH, toml_content)
    return JSONResponse({"ok": True})
