"""Token-based API authentication — fails closed when api_token is unset in config."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader, APIKeyQuery

from portal import services

_query_scheme = APIKeyQuery(name="token", auto_error=False)
_header_scheme = APIKeyHeader(name="Authorization", auto_error=False)


async def verify_token(
    query_token: str | None = Security(_query_scheme),
    header_token: str | None = Security(_header_scheme),
) -> None:
    expected = services.config().agent.api_token
    if not expected:
        # An unset token is a misconfiguration, not "auth disabled" — the
        # media API binds to 0.0.0.0 by default, so failing open here would
        # serve every library to the whole LAN. __main__._run() generates
        # and persists a token on first run, so this should not happen in
        # practice; if it does, reject rather than let requests through.
        raise HTTPException(status_code=401, detail="Unauthorized")

    provided = query_token
    if not provided and header_token:
        provided = header_token[7:] if header_token.startswith("Bearer ") else header_token

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
