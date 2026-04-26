from fastapi import APIRouter
from portal import path_registry, services

router = APIRouter()


@router.get("/libraries")
async def libraries() -> dict:
    return {
        "libraries": [
            {"name": r.name, "path": path_registry.token_for(r)}
            for r in services.roots()
        ]
    }
