"""Web UI routes — served on 127.0.0.1:5567."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from portal import indexer, services

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

router = APIRouter()
_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()
_CONFIG_PATH = Path.home() / ".portal" / "config.toml"


def _maybe_tilde(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def _cfg_to_dict() -> dict:
    cfg = services.config()
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
        f'media_api_bind = {_toml_str(str(agent.get("media_api_bind", "0.0.0.0")))}',
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
    body = await request.json()
    toml_content = _dict_to_toml(body)
    try:
        tomllib.loads(toml_content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Config validation failed: {exc}")
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(toml_content, encoding="utf-8")
    return JSONResponse({"ok": True})
