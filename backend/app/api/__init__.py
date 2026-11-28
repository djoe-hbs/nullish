from fastapi import APIRouter, Depends

from app.api import admin, ask, auth, chat, conversations, datasources, health, legal, sites, titles
from app.auth.deps import current_user

api_router = APIRouter(prefix="/api/v1")
# Public: health, auth endpoints, and public legal disclosures.
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(legal.router)
# Everything else requires a signed-in user.
signed_in = [Depends(current_user)]
api_router.include_router(chat.router, dependencies=signed_in)
api_router.include_router(ask.router, dependencies=signed_in)
api_router.include_router(conversations.router, dependencies=signed_in)
api_router.include_router(sites.router, dependencies=signed_in)
api_router.include_router(titles.router, dependencies=signed_in)
api_router.include_router(datasources.router, dependencies=signed_in)
api_router.include_router(admin.router, dependencies=signed_in)
