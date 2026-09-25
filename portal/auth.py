"""Token-based API authentication — fails closed when api_token is unset in config.

Every failed attempt is logged with the peer address, and a peer that keeps
failing is throttled (HTTP 429) for a short window. The media API is
LAN-facing with a single shared secret, so this is the only visibility an
operator has into someone guessing at the token.
"""

from __future__ import annotations

import hmac
import logging
import time

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader, APIKeyQuery

from portal import services

log = logging.getLogger(__name__)

_query_scheme = APIKeyQuery(name="token", auto_error=False)
_header_scheme = APIKeyHeader(name="Authorization", auto_error=False)

# Throttle: more than _FAIL_LIMIT failures from one peer inside _FAIL_WINDOW
# seconds gets 429 until the window drains. Generous enough that a client
# with a stale token after a re-pair isn't locked out for long, tight
# enough that guessing is pointless.
_FAIL_WINDOW = 60.0
_FAIL_LIMIT = 20
_MAX_TRACKED_PEERS = 1024
_failures: dict[str, list[float]] = {}


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _recent_failures(peer: str, now: float) -> list[float]:
    hits = [t for t in _failures.get(peer, ()) if now - t < _FAIL_WINDOW]
    if hits:
        _failures[peer] = hits
    else:
        _failures.pop(peer, None)
    return hits


def _record_failure(peer: str, now: float) -> None:
    if peer not in _failures and len(_failures) >= _MAX_TRACKED_PEERS:
        # Drop stale peers before tracking a new one so the map stays bounded.
        for stale in [p for p in _failures if not _recent_failures(p, now)]:
            _failures.pop(stale, None)
        if len(_failures) >= _MAX_TRACKED_PEERS:
            return
    _failures.setdefault(peer, []).append(now)


def _reject(request: Request, reason: str) -> HTTPException:
    peer = _peer(request)
    now = time.monotonic()
    _record_failure(peer, now)
    log.warning("Rejected %s %s from %s: %s", request.method, request.url.path, peer, reason)
    return HTTPException(status_code=401, detail="Unauthorized")


async def verify_token(
    request: Request,
    query_token: str | None = Security(_query_scheme),
    header_token: str | None = Security(_header_scheme),
) -> None:
    peer = _peer(request)
    if len(_recent_failures(peer, time.monotonic())) >= _FAIL_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts",
            headers={"Retry-After": str(int(_FAIL_WINDOW))},
        )

    expected = services.config().agent.api_token
    if not expected:
        # An unset token is a misconfiguration, not "auth disabled" — the
        # media API binds to 0.0.0.0 by default, so failing open here would
        # serve every library to the whole LAN. __main__._run() generates
        # and persists a token on first run, so this should not happen in
        # practice; if it does, reject rather than let requests through.
        raise _reject(request, "no api_token configured")

    provided = query_token
    if not provided and header_token:
        provided = header_token[7:] if header_token.startswith("Bearer ") else header_token

    if not provided:
        raise _reject(request, "missing token")

    # Compare as bytes: the str form of compare_digest raises TypeError on
    # non-ASCII input, which would turn a bad token into a 500.
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise _reject(request, "bad token")
