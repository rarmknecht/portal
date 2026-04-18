from fastapi import APIRouter
from portal import services

router = APIRouter()


@router.get("/libraries")
async def libraries() -> dict:
    return {
        "libraries": [
            {"name": r.name, "path": str(r)}
            for r in services.roots()
        ]
    }
