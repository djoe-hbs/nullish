"""Short, meaningful conversation titles written by the chat model."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from openai import APIConnectionError, APIStatusError
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.chat import ChatMessage
from app.db.models import Site
from app.db.session import SessionLocal
from app.llm.chat import ChatTarget, get_chat
from app.orchestrator.answer import fix_site_name_typos

router = APIRouter(tags=["titles"])

TITLE_PROMPT = (
    "You name chat conversations for a sidebar. Reply with the title only: 2 to 6 words, "
    "Title Case, no quotes, no emoji, no trailing punctuation. Name the topic, not the action "
    '(good: "Hudsmers Company Overview", "Book Prices on Poetry"; '
    'bad: "User Asks About Something").'
)
MAX_CHARS = 2000


class TitleRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=6)


class TitleOut(BaseModel):
    title: str


def clean_title(raw: str) -> str:
    title = raw.strip().splitlines()[0] if raw.strip() else ""
    title = re.sub(r"^(title|conversation)\s*:\s*", "", title, flags=re.I)
    title = title.strip(" \"'`*#.:;!-")
    return title[:60].strip()


@router.post("/title", response_model=TitleOut)
async def make_title(
    req: TitleRequest, chat: Annotated[ChatTarget, Depends(get_chat)]
) -> TitleOut:
    async with SessionLocal() as db:
        sites = list(await db.scalars(select(Site)))
    # Misspelled site names in the question ("hudsmres") come out spelled correctly.
    text = "\n".join(
        f"{m.role}: {fix_site_name_typos(m.content, sites)}" for m in req.messages
    )[:MAX_CHARS]
    try:
        resp = await chat.client.chat.completions.create(
            model=chat.model,
            temperature=0.2,
            max_tokens=24,
            messages=[
                {"role": "system", "content": TITLE_PROMPT},
                {"role": "user", "content": f"Conversation:\n{text}\n\nTitle:"},
            ],
        )
    except (APIConnectionError, APIStatusError) as exc:
        raise HTTPException(503, "chat model unavailable") from exc
    title = clean_title(resp.choices[0].message.content or "") if resp.choices else ""
    if not title:
        raise HTTPException(502, "the model returned no title")
    return TitleOut(title=title)
