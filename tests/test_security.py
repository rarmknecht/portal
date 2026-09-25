"""Regression tests for the security-relevant edges of the media API and UI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portal import auth, config as cfg_mod, db, path_registry, services, thumbnailer
from portal.__main__ import _build_media_app, _build_ui_app

TOKEN = "test-token-" + "x" * 40


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "lib"
    (root / "sub").mkdir(parents=True)
    (root / "a.mp4").write_bytes(b"0123456789" * 100)
    (root / "notes.txt").write_text("not media")
    (root / "empty.mp4").write_bytes(b"")

    cfg = cfg_mod.Config()
    cfg.agent.api_token = TOKEN
    cfg.data_dir = tmp_path / "data"
    cfg.thumbnails.cache_dir = tmp_path / "thumbs"
    cfg.logging.log_dir = tmp_path / "logs"
    cfg.thumbnails.cache_dir.mkdir()
    asyncio.run(db.init(cfg.db_path))

    services.init(cfg, [root.resolve()])
    auth._failures.clear()
    yield cfg, root
    auth._failures.clear()


@pytest.fixture
def api(env):
    return TestClient(_build_media_app())


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _browse(api, token=None):
    if token is None:
        token = api.get("/api/v1/libraries", headers=_auth()).json()["libraries"][0]["path"]
    r = api.get("/api/v1/browse", params={"path": token}, headers=_auth())
    assert r.status_code == 200, r.text
    return {e["name"]: e for e in r.json()["entries"]}


# --- docs -----------------------------------------------------------------

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_disabled_on_media_api(api, path):
    assert api.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_disabled_on_ui(env, path):
    ui = TestClient(_build_ui_app(), client=("127.0.0.1", 40000))
    assert ui.get(path, headers={"Host": "127.0.0.1:5567"}).status_code == 404


# --- auth -----------------------------------------------------------------

def test_health_is_open(api):
    assert api.get("/api/v1/health").status_code == 200


def test_missing_token_rejected(api):
    assert api.get("/api/v1/libraries").status_code == 401


def test_wrong_token_rejected(api):
    assert api.get("/api/v1/libraries", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_non_ascii_token_is_401_not_500(api):
    r = api.get("/api/v1/libraries", params={"token": "\u00e9"})
    assert r.status_code == 401


def test_header_and_query_token_accepted(api):
    assert api.get("/api/v1/libraries", headers=_auth()).status_code == 200
    assert api.get("/api/v1/libraries", params={"token": TOKEN}).status_code == 200


def test_repeated_failures_throttle_peer(api):
    for _ in range(auth._FAIL_LIMIT):
        assert api.get("/api/v1/libraries", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = api.get("/api/v1/libraries", headers=_auth())
    assert r.status_code == 429
    assert r.headers["Retry-After"]


# --- stream / thumbnail gating --------------------------------------------

def test_stream_refuses_non_media(api):
    entries = _browse(api)
    r = api.get("/api/v1/stream", params={"path": entries["notes.txt"]["path"]}, headers=_auth())
    assert r.status_code == 415


def test_stream_serves_media_with_nosniff(api, env):
    _, root = env
    entries = _browse(api)
    r = api.get("/api/v1/stream", params={"path": entries["a.mp4"]["path"]}, headers=_auth())
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.content == (root / "a.mp4").read_bytes()


def test_stream_range(api):
    entries = _browse(api)
    tok = entries["a.mp4"]["path"]
    r = api.get("/api/v1/stream", params={"path": tok}, headers={**_auth(), "Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["Content-Range"] == "bytes 2-5/1000"
    r = api.get("/api/v1/stream", params={"path": tok}, headers={**_auth(), "Range": "bytes=-3"})
    assert r.status_code == 206 and r.content == b"789"
    r = api.get("/api/v1/stream", params={"path": tok}, headers={**_auth(), "Range": "bytes=5000-"})
    assert r.status_code == 416
    r = api.get("/api/v1/stream", params={"path": entries["empty.mp4"]["path"]}, headers={**_auth(), "Range": "bytes=0-"})
    assert r.status_code == 416


def test_thumbnail_refuses_non_media_and_directories(api):
    entries = _browse(api)
    for name in ("notes.txt", "sub"):
        r = api.get("/api/v1/thumbnail", params={"path": entries[name]["path"]}, headers=_auth())
        assert r.status_code == 415, name


def test_unknown_token_rejected(api):
    assert api.get("/api/v1/stream", params={"path": "f" * 32}, headers=_auth()).status_code == 400


def test_token_outside_roots_rejected(api, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    tok = path_registry.token_for(outside)
    assert api.get("/api/v1/stream", params={"path": tok}, headers=_auth()).status_code == 403


# --- thumbnailer ------------------------------------------------------------

@pytest.mark.parametrize("requested,expected", [(64, 160), (160, 160), (161, 320), (640, 640), (641, 1280), (1280, 1280), (9999, 1280)])
def test_snap_size(requested, expected):
    assert thumbnailer.snap_size(requested) == expected


def test_enforce_cache_limit_evicts_oldest(tmp_path):
    cache = tmp_path / "c"
    cache.mkdir()
    for i in range(10):
        p = cache / f"{i:040d}_320.jpg"
        p.write_bytes(b"x" * 100)
        os.utime(p, (i * 1000, i * 1000))  # atime ascending with i
    removed = thumbnailer.enforce_cache_limit(cache, 500)
    assert removed >= 5
    survivors = sorted(p.name for p in cache.iterdir())
    assert survivors == sorted(f"{i:040d}_320.jpg" for i in range(10 - len(survivors), 10))
    assert thumbnailer.enforce_cache_limit(cache, 500) == 0


# --- search ---------------------------------------------------------------

def test_search_by_name_and_token_without_leaking_path(api, env):
    _, root = env
    libs = api.get("/api/v1/libraries", headers=_auth()).json()["libraries"][0]
    for ident in (libs["name"], libs["path"]):
        r = api.get("/api/v1/search", params={"library": ident, "q": "a"}, headers=_auth())
        assert r.status_code == 200
        assert r.json()["library"] == ident
        assert str(root) not in r.text
    assert api.get("/api/v1/search", params={"library": str(root), "q": "a"}, headers=_auth()).status_code == 404


# --- UI loopback guard ------------------------------------------------------

def test_ui_rejects_non_loopback_peer(env):
    ui = TestClient(_build_ui_app(), client=("192.168.1.20", 40000))
    assert ui.get("/", headers={"Host": "127.0.0.1:5567"}).status_code == 403


def test_ui_rejects_rebound_host(env):
    ui = TestClient(_build_ui_app(), client=("127.0.0.1", 40000))
    assert ui.get("/", headers={"Host": "evil.example:5567"}).status_code == 400
    assert ui.get("/", headers={"Host": "127.0.0.1:5567"}).status_code == 200


def test_ui_rejects_cross_origin_post(env):
    ui = TestClient(_build_ui_app(), client=("127.0.0.1", 40000))
    r = ui.post("/api/config", json={}, headers={"Host": "127.0.0.1:5567", "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = ui.post("/api/config", json={}, headers={"Host": "127.0.0.1:5567"})
    assert r.status_code == 403
