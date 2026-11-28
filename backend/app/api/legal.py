"""Public read-only endpoint for legal metadata and customized legal documents."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.legal_store import LegalSettingsData, get_legal_settings

router = APIRouter(tags=["legal"])

DB = Annotated[AsyncSession, Depends(get_session)]


@router.get("/legal", response_model=LegalSettingsData)
async def read_public_legal(db: DB) -> LegalSettingsData:
    return await get_legal_settings(db)

