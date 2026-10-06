from dataclasses import dataclass
from datetime import date

from app.domain.types import JsonObject


@dataclass(frozen=True)
class ParsedSection:
    section_ref: str
    heading: str | None
    paragraphs: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class SourceDocument:
    """One Federal Register document after fetch, before the quality gate."""

    document_number: str
    title: str
    document_type: str
    publication_date: date | None
    effective_on: date | None
    citation: str | None
    source_url: str | None
    cfr_references: tuple[JsonObject, ...]
    docket_ids: tuple[str, ...]
    correction_of: str | None
    sections: tuple[ParsedSection, ...]
    source_xml_sha256: str | None = None
    parse_error: str | None = None
