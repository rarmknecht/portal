"""Web UI routes — served on 127.0.0.1:5567."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from portal import services

router = APIRouter()
_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


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
    })
