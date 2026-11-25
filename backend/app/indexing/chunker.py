"""Heading-aware markdown chunker.

Paragraphs are packed into chunks of ~target_chars, never splitting a paragraph unless it alone
is too long (then by sentence, then hard). Consecutive chunks share ~overlap_chars of text.
Each chunk records the heading path ("Shipping > Returns") of where it starts.
"""

import math
import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


@dataclass
class ChunkPiece:
    heading_path: str
    text: str
    tokens: int


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _blocks(markdown: str) -> list[tuple[str, str]]:
    """(heading_path, paragraph) pairs. Heading lines stay in the text as their own block."""
    stack: list[tuple[int, str]] = []
    blocks: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", markdown):
        para = para.strip()
        if not para:
            continue
        first = para.split("\n", 1)[0]
        m = HEADING.match(first)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        path = " > ".join(t for _, t in stack)
        blocks.append((path, para))
    return blocks


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    cur = ""
    for sent in SENTENCE_END.split(text):
        while len(sent) > limit:  # one giant "sentence" (tables, minified text): hard split
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > limit:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts


def _tail(text: str, n: int) -> str:
    if n <= 0 or len(text) <= n:
        return ""
    cut = text.find(" ", len(text) - n)
    return text[cut + 1:] if cut != -1 else ""


def chunk_markdown(
    markdown: str, target_chars: int = 2000, overlap_chars: int = 300
) -> list[ChunkPiece]:
    pieces: list[tuple[str, str]] = []
    for path, para in _blocks(markdown):
        for part in _split_long(para, target_chars):
            pieces.append((path, part))

    chunks: list[ChunkPiece] = []
    cur: list[str] = []
    cur_len = 0
    cur_path = ""

    def emit() -> None:
        text = "\n\n".join(cur).strip()
        if text:
            chunks.append(ChunkPiece(cur_path, text, estimate_tokens(text)))

    for path, part in pieces:
        if cur and cur_len + len(part) + 2 > target_chars:
            emit()
            overlap = _tail("\n\n".join(cur), overlap_chars)
            cur = [overlap] if overlap else []
            cur_len = len(overlap)
            cur_path = path
        if not cur:
            cur_path = path
        cur.append(part)
        cur_len += len(part) + 2
    emit()
    return chunks
