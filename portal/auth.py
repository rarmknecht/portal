"""Token-based API authentication — no-op when api_token is unset in config."""

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
        return  # auth disabled — server runs open on the LAN

    provided = query_token
    if not provided and header_token:
        provided = header_token[7:] if header_token.startswith("Bearer ") else header_token

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
