"""Split long sections on paragraph boundaries so citation quotes stay exact.

Chunks keep the parent section reference, with `#2` onward for later pieces.
The overlapping tail is copied into the next chunk, so a quote that crosses the
boundary can still be found in one stored section.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.ingestion.federal_register.records import ParsedSection, fit_ref

MAX_SECTION_WORDS = 350
MIN_CHUNK_WORDS = 250
OVERLAP_WORDS = 50
MIN_SECTION_WORDS = 20


@dataclass(frozen=True)
class SectionChunk:
    section_ref: str
    heading: str | None
    text: str
    position: int


def prepare_sections(
    sections: Sequence[ParsedSection],
) -> tuple[tuple[SectionChunk, ...], int, int]:
    """Drop short sections, split long ones, and number the chunks that remain."""
    kept: list[SectionChunk] = []
    dropped = 0
    split = 0
    for section in sections:
        if section.word_count < MIN_SECTION_WORDS:
            dropped += 1
            continue
        parts = _chunk_paragraphs(section)
        if len(parts) > 1:
            split += 1
        for index, text in enumerate(parts):
            suffix = "" if index == 0 else f"#{index + 1}"
            kept.append(
                SectionChunk(
                    section_ref=fit_ref(section.section_ref, suffix),
                    heading=section.heading,
                    text=text,
                    position=len(kept),
                )
            )
    return tuple(kept), dropped, split


def _chunk_paragraphs(section: ParsedSection) -> list[str]:
    paragraphs = [paragraph for paragraph in section.paragraphs if paragraph.strip()]
    if section.word_count <= MAX_SECTION_WORDS or len(paragraphs) < 2:
        return ["\n\n".join(paragraphs)]

    chunks: list[list[str]] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        count = len(paragraph.split())
        overflows = (
            current
            and current_words >= MIN_CHUNK_WORDS
            and current_words + count > MAX_SECTION_WORDS
        )
        if overflows:
            chunks.append(current)
            overlap = _tail_words(current, OVERLAP_WORDS)
            current = [overlap] if overlap else []
            current_words = len(overlap.split()) if overlap else 0
        current.append(paragraph)
        current_words += count
    if current:
        chunks.append(current)
    return ["\n\n".join(chunk) for chunk in chunks]


def _tail_words(paragraphs: Sequence[str], count: int) -> str:
    words = " ".join(paragraphs).split()
    if len(words) <= count:
        return " ".join(words)
    return " ".join(words[-count:])
