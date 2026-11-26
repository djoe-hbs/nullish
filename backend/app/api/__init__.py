from fastapi import APIRouter, Depends

from app.api import ask, auth, chat, datasources, health, sites, titles
from app.auth.deps import current_user

api_router = APIRouter(prefix="/api/v1")
# Public: health (no secrets) and the login endpoints themselves.
api_router.include_router(health.router)
api_router.include_router(auth.router)
# Everything else requires a signed-in user.
signed_in = [Depends(current_user)]
api_router.include_router(chat.router, dependencies=signed_in)
api_router.include_router(ask.router, dependencies=signed_in)
api_router.include_router(sites.router, dependencies=signed_in)
api_router.include_router(titles.router, dependencies=signed_in)
api_router.include_router(datasources.router, dependencies=signed_in)
