"""Database-backed legal documents and operator metadata (persisted in `settings` under 'legal').

Overrides front-end built-in defaults dynamically without rebuilding/restarting containers.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

LEGAL_KEY = "legal"

LegalDocId = Literal["privacy", "terms", "disclaimer"]


class LegalMeta(BaseModel):
    service_name: str = Field("Smish", min_length=1, max_length=120)
    operator_name: str = Field("Harish Rohit Israel", min_length=1, max_length=200)
    operator_website: str = Field("https://smish.io/", min_length=1, max_length=300)
    contact_email: str = Field("reachus@smish.io", min_length=3, max_length=320)
    privacy_email: str = Field("reachus@smish.io", min_length=3, max_length=320)
    jurisdiction: str = Field("Tamil Nadu, India", min_length=2, max_length=200)
    effective_date: str = Field("Nov 28, 2026", min_length=2, max_length=120)
    inference_provider: str = Field(
        "self-hosted GPU infrastructure", min_length=2, max_length=300
    )
    training_commitment: str = Field(
        "We do not sell your personal data, and we do not use your configured websites, "
        "database records, or chat prompts to train public or third-party foundation models.",
        min_length=5,
        max_length=2000,
    )
    liability_cap: str = Field(
        "THE TOTAL FEES ACTUALLY PAID BY YOU FOR THE SERVICE IN THE TWELVE (12) MONTHS "
        "IMMEDIATELY PRECEDING THE CLAIM, OR THE MINIMUM AMOUNT PERMITTED BY APPLICABLE LAW "
        "IF ACCESS IS PROVIDED FREE OF CHARGE",
        min_length=2,
        max_length=1000,
    )


class LegalParagraphBlock(BaseModel):
    kind: Literal["p"] = "p"
    text: str = Field(min_length=1, max_length=10_000)


class LegalListBlock(BaseModel):
    kind: Literal["ul"] = "ul"
    items: list[str] = Field(min_length=1, max_length=100)


class LegalDlItem(BaseModel):
    term: str = Field(min_length=1, max_length=300)
    detail: str = Field(min_length=1, max_length=5_000)


class LegalDlBlock(BaseModel):
    kind: Literal["dl"] = "dl"
    items: list[LegalDlItem] = Field(min_length=1, max_length=100)


LegalBlockData = Annotated[
    LegalParagraphBlock | LegalListBlock | LegalDlBlock,
    Field(discriminator="kind"),
]


class LegalSectionData(BaseModel):
    heading: str = Field(min_length=1, max_length=300)
    blocks: list[LegalBlockData] = Field(min_length=1, max_length=50)


class LegalDocumentData(BaseModel):
    id: LegalDocId
    title: str = Field(min_length=1, max_length=120)
    subtitle: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=2000)
    sections: list[LegalSectionData] = Field(min_length=1, max_length=60)


class LegalSettingsData(BaseModel):
    meta: LegalMeta = Field(default_factory=LegalMeta)
    documents: dict[LegalDocId, LegalDocumentData] = Field(default_factory=dict)


async def get_legal_settings(db: AsyncSession) -> LegalSettingsData:
    row = await db.get(Setting, LEGAL_KEY)
    if row is None or not isinstance(row.value, dict):
        return LegalSettingsData()
    try:
        return LegalSettingsData.model_validate(row.value)
    except Exception:  # noqa: BLE001 - corrupt row falls back safely to defaults
        return LegalSettingsData()


async def save_legal_settings(
    db: AsyncSession, data: LegalSettingsData
) -> LegalSettingsData:
    # Ensure document `id` fields always match their dictionary keys.
    docs = {
        doc_id: doc.model_copy(update={"id": doc_id})
        for doc_id, doc in data.documents.items()
    }
    clean = LegalSettingsData(meta=data.meta, documents=docs)
    payload = clean.model_dump()
    row = await db.get(Setting, LEGAL_KEY)
    if row is None:
        db.add(Setting(key=LEGAL_KEY, value=payload))
    else:
        row.value = payload
    await db.commit()
    return clean


async def reset_legal_settings(db: AsyncSession) -> LegalSettingsData:
    row = await db.get(Setting, LEGAL_KEY)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return LegalSettingsData()

