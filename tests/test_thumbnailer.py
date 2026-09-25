"""Thumbnailer: only real images may be produced, cached, or served."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from portal import thumbnailer

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
H264_PACKET = b"\x00\x00\x02\xaf\x06\x05\xff\xff" + b"\x00" * 64


@pytest.mark.parametrize(
    "data, expected",
    [(JPEG, True), (PNG, True), (H264_PACKET, False), (b"", False), (b"\xff\xd8", False)],
)
def test_is_image(data, expected):
    assert thumbnailer.is_image(data) is expected


class _FakeProc:
    """Stands in for the ffmpeg subprocess: writes *output* to the -y
    destination (last argv element) and exits with *rc*."""

    def __init__(self, cmd, output, rc):
        self.pid = None
        self.returncode = None
        self._dest = Path(cmd[-1])
        self._output = output
        self._rc = rc

    async def wait(self):
        if self._output is not None:
            self._dest.write_bytes(self._output)
        self.returncode = self._rc
        return self._rc


def _fake_ffmpeg(monkeypatch, outputs_by_pass):
    """outputs_by_pass: list of (output_bytes_or_None, rc), consumed per call.
    Returns the list of argv lists actually run."""
    calls: list[list[str]] = []

    async def fake_exec(*cmd, **_kw):
        calls.append(list(cmd))
        output, rc = outputs_by_pass.pop(0)
        return _FakeProc(cmd, output, rc)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def _get(path, cache, **kw):
    return asyncio.run(thumbnailer.get_thumbnail(path, cache_dir=cache, size=320, **kw))


def test_video_skips_embedded_pass_and_uses_frame_grab(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, [(JPEG, 0)])
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    data = _get(video, tmp_path, prefer_embedded=True)

    assert data == JPEG
    assert len(calls) == 1
    assert "-vcodec" not in calls[0] and "-ss" in calls[0]
    assert list(tmp_path.glob("*_320.jpg"))[0].read_bytes() == JPEG


def test_audio_uses_embedded_pass_first(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, [(JPEG, 0)])
    track = tmp_path / "song.mp3"
    track.write_bytes(b"x")

    assert _get(track, tmp_path, prefer_embedded=True) == JPEG
    assert len(calls) == 1
    assert calls[0][calls[0].index("-vcodec") + 1] == "copy"


def test_non_image_output_is_discarded_not_cached(tmp_path, monkeypatch):
    # Even if a pass exits 0 with a raw packet, nothing may be cached.
    calls = _fake_ffmpeg(monkeypatch, [(H264_PACKET, 0), (H264_PACKET, 0)])
    track = tmp_path / "song.mp3"
    track.write_bytes(b"x")

    assert _get(track, tmp_path, prefer_embedded=True) is None
    assert len(calls) == 2  # embedded pass rejected, frame grab tried too
    assert list(tmp_path.glob("*.jpg")) == []


def test_cached_thumbnail_is_served_without_ffmpeg(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, [])
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    (tmp_path / thumbnailer._cache_key(video, 320)).write_bytes(JPEG)

    assert _get(video, tmp_path) == JPEG
    assert calls == []
