"""Quality checks that decide whether a Federal Register pull may be loaded.

A critical failure leaves the curated tables unchanged. Counts are still written
so a bad pull can be inspected without guessing which check fired.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from app.domain.enums import PolicyStatus
from app.domain.types import JsonObject
from app.ingestion.federal_register.chunking import SectionChunk, prepare_sections
from app.ingestion.federal_register.normalize import has_residual_markup
from app.ingestion.federal_register.records import SourceDocument

_PARSE_FAILURE_LIMIT = 0.05


class QualityGateError(Exception):
    """Curated tables were not changed because the pull failed a critical check."""


@dataclass(frozen=True)
class CuratedDocument:
    document_number: str
    title: str
    document_type: str
    publication_date: date
    effective_on: date | None
    effective_from: date
    effective_date_source: str
    citation: str | None
    source_url: str | None
    cfr_references: tuple[JsonObject, ...]
    docket_ids: tuple[str, ...]
    correction_of: str | None
    sections: tuple[SectionChunk, ...]
    content_sha256: str
    source_xml_sha256: str | None


@dataclass(frozen=True)
class QualityReport:
    run_id: str
    documents: int
    parse_failures: int
    duplicate_document_numbers: int
    empty_sections_dropped: int
    sections_split: int
    residual_markup: int
    date_failures: int
    documents_without_sections: int
    counts_by_cfr_part: dict[str, int]
    counts_by_document_type: dict[str, int]
    blocking: bool
    reasons: tuple[str, ...]

    def as_json(self) -> JsonObject:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def prepare_corpus(
    documents: Sequence[SourceDocument],
    *,
    run_id: str,
) -> tuple[QualityReport, tuple[CuratedDocument, ...]]:
    dropped = 0
    split = 0
    residual = 0
    date_failures = 0
    missing_fields = 0
    without_sections = 0
    curated: list[CuratedDocument] = []
    parse_failures = sum(1 for document in documents if document.parse_error)
    numbers = [document.document_number for document in documents]
    duplicates = len(numbers) - len(set(numbers))
    type_counts: Counter[str] = Counter()
    part_counts: Counter[str] = Counter()

    for document in documents:
        type_counts[document.document_type or "unknown"] += 1
        for reference in document.cfr_references:
            part = reference.get("part")
            if isinstance(part, str) and part:
                part_counts[part] += 1
        if not document.document_number or not document.title or not document.document_type:
            missing_fields += 1
        if document.publication_date is None:
            date_failures += 1
            continue
        if document.parse_error:
            continue
        chunks, section_drops, section_splits = prepare_sections(document.sections)
        dropped += section_drops
        split += section_splits
        markup = sum(1 for chunk in chunks if has_residual_markup(chunk.text))
        residual += markup
        if not chunks:
            without_sections += 1
            continue
        if markup:
            continue
        effective_from, effective_source = _effective_from(document)
        body = "\n\n".join(chunk.text for chunk in chunks)
        curated.append(
            CuratedDocument(
                document_number=document.document_number,
                title=document.title,
                document_type=document.document_type,
                publication_date=document.publication_date,
                effective_on=document.effective_on,
                effective_from=effective_from,
                effective_date_source=effective_source,
                citation=document.citation,
                source_url=document.source_url,
                cfr_references=document.cfr_references,
                docket_ids=document.docket_ids,
                correction_of=document.correction_of,
                sections=chunks,
                content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                source_xml_sha256=document.source_xml_sha256,
            )
        )

    reasons: list[str] = []
    if missing_fields:
        reasons.append("missing_required_fields")
    if duplicates:
        reasons.append("duplicate_document_numbers")
    if documents and parse_failures / len(documents) > _PARSE_FAILURE_LIMIT:
        reasons.append("xml_parse_failure_rate")
    if residual:
        reasons.append("residual_markup")
    if date_failures:
        reasons.append("unparsed_dates")
    if documents and not curated and "xml_parse_failure_rate" not in reasons:
        reasons.append("no_sections_after_quality")
    report = QualityReport(
        run_id=run_id,
        documents=len(documents),
        parse_failures=parse_failures,
        duplicate_document_numbers=duplicates,
        empty_sections_dropped=dropped,
        sections_split=split,
        residual_markup=residual,
        date_failures=date_failures,
        documents_without_sections=without_sections,
        counts_by_cfr_part=dict(sorted(part_counts.items())),
        counts_by_document_type=dict(sorted(type_counts.items())),
        blocking=bool(reasons),
        reasons=tuple(reasons),
    )
    if report.blocking:
        return report, ()
    return report, tuple(curated)


def write_quality_report(path: Path, report: QualityReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def policy_status_for(document_type: str) -> PolicyStatus:
    """Final rules are active. Proposed rules and notices stay draft and are not retrieved."""
    normalized = " ".join(document_type.casefold().split())
    if normalized in {"rule", "final rule"}:
        return PolicyStatus.ACTIVE
    return PolicyStatus.DRAFT


def _effective_from(document: SourceDocument) -> tuple[date, str]:
    if document.effective_on is not None:
        return document.effective_on, "effective_on"
    if document.publication_date is None:
        raise QualityGateError("publication date is required before loading")
    return document.publication_date, "publication_date"
