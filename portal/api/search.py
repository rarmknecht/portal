from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from portal import db, path_registry, services
from portal.allowlist import within_any

router = APIRouter()


def _match_root(library: str, roots: list[Path]) -> Path | None:
    """Find the library root named by *library*: either the opaque token
    /libraries handed out for it, or its display name (kept for clients
    that predate tokens)."""
    for r in roots:
        if library == path_registry.token_for(r):
            return r
    return next((r for r in roots if r.name == library), None)


@router.get("/search")
async def search(
    library: str = Query(..., max_length=4096),
    q: str = Query(..., min_length=1, max_length=256),
) -> dict:
    roots = services.roots()
    matched_root = _match_root(library, roots)
    if matched_root is None:
        raise HTTPException(status_code=404, detail="Library not found")

    cfg = services.config()
    records = await db.search(cfg.db_path, str(matched_root), q)

    # Defensive re-check: the indexer only stores resolved, in-root paths
    # going forward, but a pre-existing row from an old index (or a stale
    # entry the watchdog raced) could still carry a lexical symlink path
    # that escapes the allowlist. Never let such a row mint a token or
    # surface name/type/size in a response.
    resolved_roots = [r.resolve() for r in roots]
    results = []
    for r in records:
        try:
            real = Path(r.path).resolve()
        except OSError:
            continue
        if not within_any(real, resolved_roots):
            continue
        results.append({"path": path_registry.token_for(real), "name": real.name, "type": r.media_type, "size": r.size})

    # Echo the client's own identifier back rather than the root's real
    # filesystem path — every other response only ever exposes tokens.
    return {
        "query": q,
        "library": library,
        "results": results,
    }
