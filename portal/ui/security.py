"""Loopback-only guard for the Portal web UI.

The web UI (``portal/ui/routes.py``) has no authentication at all — no
login, no session, no CSRF token — because it is meant to be reachable
only from the machine it runs on, bound to 127.0.0.1. It also exposes
``GET /api/config`` (which returns the API token) and ``POST /api/config``
(which rewrites the config, including ``web_ui_bind`` itself), so trusting
the wrong client here is a real credential and takeover risk, not just a
theoretical one.

The primary defense, enforced first on every request, is the actual
transport-level peer: the ASGI ``scope["client"]`` tuple. Headers are
supplied by the client and can be forged by anything that isn't a browser
(``curl -H "Host: 127.0.0.1:5567"`` costs nothing), so they cannot be the
basis of a trust decision by themselves — only the peer address the
server's own transport observed can't be spoofed by the request. We
require that peer to be loopback (127.0.0.0/8, ::1, or an IPv4-mapped
loopback address) or, for Unix domain sockets (where there is no peer
*address* at all), that the transport itself is a UDS rather than TCP.

Once the peer check passes, we still run two header checks as
defense-in-depth against anti-rebinding/CSRF tricks an ordinary browser on
the *same* loopback machine could otherwise pull off:

  * DNS rebinding: an attacker-controlled hostname can be resolved to
    127.0.0.1 *after* the browser's same-origin checks for that hostname
    have already passed, letting page JS talk to our loopback server as
    if it were same-origin.
  * Cross-site requests: any page on any origin can point a <form> (with
    enctype="text/plain") or a no-CORS-preflight ``fetch`` at
    ``http://127.0.0.1:<port>/...`` and the browser sends it anyway,
    because loopback services aren't covered by CORS preflight rules.

Because this is a single-user tool with nothing to log in to, we don't
need sessions or CSRF tokens — we only need to prove the request really
originated from a page served by *this* origin. That's enough for a
loopback-bound service and is enforced here at the ASGI layer for every
request to the UI app, after the peer check:

  1. ``Host`` must name the loopback address/port this server is actually
     listening on. A rebound hostname will never satisfy this, so DNS
     rebinding is defeated regardless of what IP it resolves to.
  2. For state-changing methods (POST/PUT/PATCH/DELETE), ``Origin`` (or,
     when a browser omits it, ``Sec-Fetch-Site``) must show the request
     came from our own origin. This defeats cross-site form/fetch
     submissions. Modern browsers always send one of these two headers on
     cross-origin requests, so requiring one (and rejecting when both are
     absent) does not weaken the check for real browser traffic — it only
     affects tools like curl, which can simply add an ``Origin`` header.

A third check — requiring ``Content-Type: application/json`` on
``POST /api/config`` — lives next to that route in ``routes.py`` since
it's specific to that one endpoint (it closes the "text/plain form post"
trick some CSRF PoCs use to avoid ever setting a real Origin/Content-Type).
"""

from __future__ import annotations

import ipaddress
from typing import Callable

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _valid_hosts(port: int) -> set[str]:
    """Host header values that legitimately name this loopback server."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if port == 80:
        # Browsers omit the port from Host when it's the scheme default.
        hosts |= {"127.0.0.1", "localhost", "[::1]", "::1"}
    return hosts


def _is_loopback_peer(scope: Scope) -> bool:
    """Whether the ASGI transport's actual peer is loopback.

    This is the primary trust check: unlike headers, ``scope["client"]``
    and ``scope["server"]`` are set by the server's own transport (uvicorn)
    from the real socket, not supplied by the client, so they can't be
    forged by a request.
    """
    client = scope.get("client")

    if client is not None:
        host = client[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if ip.is_loopback:
            return True
        mapped = getattr(ip, "ipv4_mapped", None)
        return mapped is not None and mapped.is_loopback

    # No peer address at all — this is how uvicorn reports Unix domain
    # socket connections (there's no host:port to give). Accept only when
    # the server side is also UDS-shaped (None, or a filesystem path
    # rather than a TCP host), so an unknown/unreported TCP peer is still
    # rejected rather than trusted by default.
    server = scope.get("server")
    if server is None:
        return True
    server_host = server[0] if server else None
    return isinstance(server_host, str) and "/" in server_host


def _valid_origins(port: int) -> set[str]:
    """Origin header values that legitimately point at this loopback server."""
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    }
    if port == 80:
        origins |= {"http://127.0.0.1", "http://localhost", "http://[::1]"}
    return origins


class LoopbackOnlyMiddleware:
    """ASGI middleware that rejects any request not addressed to, and not
    originating from, this loopback-bound UI server.

    ``get_port`` is a callable (rather than a fixed int) because the port
    comes from config that may not be loaded yet at the time the FastAPI
    app object is constructed — it's resolved fresh on every request.
    """

    def __init__(self, app: ASGIApp, get_port: Callable[[], int]) -> None:
        self.app = app
        self._get_port = get_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        rejection = self._check(request, self._get_port())
        if rejection is not None:
            status_code, detail = rejection
            response = PlainTextResponse(detail, status_code=status_code)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _check(self, request: Request, port: int) -> tuple[int, str] | None:
        # 0. The actual transport peer must be loopback — this is the real
        #    trust boundary. Headers below are client-supplied and can be
        #    forged by any non-browser client (e.g. curl), so they only
        #    matter once we already know the connection itself is local.
        if not _is_loopback_peer(request.scope):
            return 403, "UI is loopback-only"

        # 1. Host header must name this loopback server — blocks DNS rebinding.
        host = (request.headers.get("host") or "").strip().lower()
        if host not in _valid_hosts(port):
            return 400, "Invalid Host header"

        # 2. For state-changing methods, require same-origin evidence —
        #    blocks cross-site form/fetch submissions.
        if request.method.upper() in _STATE_CHANGING_METHODS:
            origin = request.headers.get("origin")
            if origin is not None:
                if origin.strip().lower() not in _valid_origins(port):
                    return 403, "Cross-origin request rejected"
            else:
                # No Origin header — modern browsers still send Sec-Fetch-Site
                # on every request. Its absence means this isn't a browser
                # navigation/fetch we can vouch for, so reject rather than
                # trust it.
                sec_fetch_site = request.headers.get("sec-fetch-site")
                if sec_fetch_site not in ("same-origin", "none"):
                    return 403, "Cross-origin request rejected"

        return None
