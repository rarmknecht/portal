# Portal

**A mini Plex that operates within your local network or tailnet, no subscription fees, no data collected by third-parties.**

Portal is a lightweight personal media server. It runs on your desktop or home server, indexes your local video, audio, and photo libraries, and streams them to your phone on demand. Nothing leaves your network.

The companion mobile app is [portal-app](https://github.com/rarmknecht/portal-app).

---

## How it works

```
┌─────────────────────┐              ┌─────────────────────┐
│   portal-app        │              │   portal (this)     │
│   Android / iOS     │ ◄── HTTP ──► │   your desktop or   │
│                     │    LAN or    │   home server       │
│   discovers, browses│    Tailscale │                     │
│   and streams media │              │   indexes + streams │
└─────────────────────┘              └─────────────────────┘
```

- **Agent** (this repo) runs on Windows, macOS, or Linux.
- **Mobile app** discovers the agent via mDNS on the local network, or connects manually over a Tailscale tailnet.
- Media is streamed directly — no transcoding, no cloud relay, no accounts.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [FFmpeg](https://ffmpeg.org/download.html) on your PATH (required for thumbnails and media metadata)

---

## Installation

### With uv (recommended)

```bash
git clone https://github.com/rarmknecht/portal
cd portal
uv sync
uv run portal
```

### With pip

```bash
git clone https://github.com/rarmknecht/portal
cd portal
pip install .
portal
```

On first launch Portal creates `~/.portal/` and starts with no libraries configured. Open the web UI to add your folders.

---

## Configuration

The web UI at **http://localhost:5567** lets you manage all settings without touching a file. Changes are saved to `~/.portal/config.toml` and take effect on the next restart.

If you prefer to edit the file directly, the full schema with defaults:

```toml
[agent]
media_api_bind = "0.0.0.0"   # interface the mobile app connects to
media_api_port = 7842
web_ui_bind    = "127.0.0.1" # web UI is localhost-only by default
web_ui_port    = 5567
api_token      = ""           # optional shared secret; leave blank to disable

[libraries]
allowlist = [
    "/home/you/Videos",
    "/home/you/Music",
    "/home/you/Pictures",
]

[indexing]
mode            = "background" # scan on startup, then watch for changes
scan_on_startup = true

[thumbnails]
cache_dir         = "~/.portal/thumbnails"
max_cache_size_mb = 500
prefer_embedded   = true  # use embedded album/cover art when present

[logging]
log_dir    = "~/.portal/logs"
max_size_mb = 150
rotation   = "size"
```

**Tip for Tailscale users:** set `media_api_bind` to your Tailscale interface IP (e.g. `100.x.x.x`) so the port is only reachable through the tailnet.

---

## Security

Portal is designed for **trusted networks**. The media API binds to all interfaces by default so your phone can reach it, but it has no concept of user accounts. The optional `api_token` adds a shared-secret check on every request.

**Do not expose Portal directly to the public internet.** Use [Tailscale](https://tailscale.com) (or a VPN of your choice) for remote access — it provides the authenticated encrypted tunnel that Portal intentionally delegates rather than reinventing.

---

## Supported formats

Portal streams media as-is with no transcoding. Playback depends on what the mobile OS supports natively.

| Type   | Works reliably                  |
|--------|---------------------------------|
| Video  | H.264, H.265 in MP4 / MOV      |
| Audio  | AAC, MP3, FLAC                  |
| Photos | JPEG, PNG, HEIC                 |

Files in unsupported formats are listed in the app but marked unplayable rather than hidden.

---

## License

[MIT](LICENSE)
