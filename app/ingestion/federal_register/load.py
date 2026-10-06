"""Idempotent load of curated Federal Register documents into policy versions.

A document number keeps its current version while the normalized text is unchanged.
A new hash retires that version and inserts the next one, so retrieval does not
see two active copies of the same rule.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import IngestionStatus, PolicyStatus
from app.domain.models import IngestionRun, Policy, PolicySection, new_id, utc_now
from app.domain.types import JsonObject
from app.ingestion.federal_register.client import (
    FederalRegisterClient,
    FederalRegisterDocument,
    FederalRegisterError,
)
from app.ingestion.federal_register.parse import FederalRegisterParseError, parse_document_body
from app.ingestion.federal_register.quality import (
    CuratedDocument,
    QualityGateError,
    QualityReport,
    policy_status_for,
    prepare_corpus,
    write_quality_report,
)
from app.ingestion.federal_register.records import ParsedSection, SourceDocument


@dataclass(frozen=True)
class LoadStats:
    inserted: int
    unchanged: int
    sections: int


def load_curated(
    session: Session,
    documents: tuple[CuratedDocument, ...] | list[CuratedDocument],
    *,
    report: QualityReport,
    dataset_version: str,
) -> LoadStats:
    if report.blocking:
        raise QualityGateError(", ".join(report.reasons) or "quality gate failed")
    inserted = 0
    unchanged = 0
    sections = 0
    for document in documents:
        if _upsert(session, document, dataset_version):
            inserted += 1
            sections += len(document.sections)
        else:
            unchanged += 1
    return LoadStats(inserted=inserted, unchanged=unchanged, sections=sections)


def execute_ingest(
    session: Session,
    client: FederalRegisterClient,
    *,
    agency: str,
    document_type: str,
    cfr_title: int,
    since: date,
    until: date,
    max_documents: int,
    incremental: bool,
    data_dir: Path,
    git_sha: str | None,
    run_id: str | None = None,
) -> int:
    """Search, check, and load. Returns 0 when the curated tables were updated or left valid."""
    window_start = since
    if incremental:
        watermark = latest_watermark(session)
        if watermark is not None and watermark >= window_start:
            window_start = watermark + timedelta(days=1)
    params: JsonObject = {
        "agency": agency,
        "document_type": document_type,
        "cfr_title": cfr_title,
        "since": window_start.isoformat(),
        "until": until.isoformat(),
        "max_documents": max_documents,
        "incremental": incremental,
    }
    run = IngestionRun(
        run_id=run_id or new_id(),
        params=params,
        counts={"documents": 0},
        status=IngestionStatus.RUNNING,
        started_at=utc_now(),
    )
    session.add(run)
    session.commit()
    if window_start > until:
        _finish(
            session,
            run,
            IngestionStatus.SUCCEEDED,
            {"documents": 0, "inserted": 0},
            None,
            None,
        )
        print("ingested 0 documents, 0 unchanged, 0 parse failures")
        return 0

    try:
        fetched = client.search_documents(
            agency=agency,
            document_type=document_type,
            cfr_title=cfr_title,
            since=window_start,
            until=until,
            max_documents=max_documents,
        )
    except FederalRegisterError as exc:
        _finish(session, run, IngestionStatus.FAILED, {"documents": 0}, None, str(exc))
        print("ingestion blocked: search failed")
        return 1
    sources = tuple(_materialize(client, document) for document in fetched)
    report, curated = prepare_corpus(sources, run_id=run.run_id)
    write_quality_report(data_dir / "quality" / f"{run.run_id}.json", report)
    if report.blocking:
        _finish(
            session,
            run,
            IngestionStatus.FAILED,
            _counts(report, LoadStats(0, 0, 0)),
            None,
            ", ".join(report.reasons),
        )
        print(f"ingestion blocked: {', '.join(report.reasons)}")
        return 1

    staging_path = data_dir / "staging" / run.run_id / "sections.jsonl"
    _write_staging(staging_path, curated)
    version = _write_manifest(
        data_dir / "manifests",
        curated,
        query=params,
        git_sha=git_sha,
        staging_path=staging_path,
    )
    stats = load_curated(session, curated, report=report, dataset_version=version)
    watermark = max((document.publication_date for document in curated), default=None)
    if watermark is not None:
        params["watermark"] = watermark.isoformat()
        run.params = params
    _finish(session, run, IngestionStatus.SUCCEEDED, _counts(report, stats), version, None)
    print(
        f"ingested {stats.inserted} documents, {stats.unchanged} unchanged, "
        f"{report.parse_failures} parse failures"
    )
    return 0


def latest_watermark(session: Session) -> date | None:
    runs = session.scalars(
        select(IngestionRun)
        .where(IngestionRun.status == IngestionStatus.SUCCEEDED)
        .order_by(IngestionRun.finished_at.desc())
    )
    for run in runs:
        raw = run.params.get("watermark")
        if isinstance(raw, str):
            return date.fromisoformat(raw)
    return None


def _upsert(session: Session, document: CuratedDocument, dataset_version: str) -> bool:
    latest = session.scalar(
        select(Policy)
        .where(Policy.document_number == document.document_number)
        .order_by(Policy.version.desc())
    )
    if latest is not None and latest.content_hash == document.content_sha256:
        return False
    version = 1 if latest is None else latest.version + 1
    if latest is not None and latest.status != PolicyStatus.RETIRED:
        latest.status = PolicyStatus.RETIRED
    content = "\n\n".join(section.text for section in document.sections)
    session.add(
        Policy(
            name=document.document_number[:160],
            version=version,
            status=policy_status_for(document.document_type),
            content=content,
            content_hash=document.content_sha256,
            content_sha256=document.content_sha256,
            effective_from=document.effective_from,
            document_number=document.document_number,
            title=document.title,
            source="federal_register",
            source_url=document.source_url,
            document_type=document.document_type,
            publication_date=document.publication_date,
            citation=document.citation,
            cfr_references=list(document.cfr_references),
            docket_ids=list(document.docket_ids),
            effective_date_source=document.effective_date_source,
            dataset_version=dataset_version,
            correction_of=document.correction_of,
            created_by="federal-register",
            sections=[
                PolicySection(
                    section_ref=section.section_ref,
                    heading=section.heading,
                    text=section.text,
                    position=section.position,
                )
                for section in document.sections
            ],
        )
    )
    session.flush()
    return True


def _materialize(
    client: FederalRegisterClient,
    document: FederalRegisterDocument,
) -> SourceDocument:
    references = tuple(_reference_payload(document))
    dockets = tuple(document.docket_ids)
    try:
        body = client.fetch_document_body(document)
    except FederalRegisterError as exc:
        return _source_document(document, references, dockets, (), str(exc)[:240], None)
    try:
        sections = tuple(parse_document_body(body, document.document_number))
        error = None
    except FederalRegisterParseError as exc:
        sections = ()
        error = str(exc)[:240]
    return _source_document(
        document,
        references,
        dockets,
        sections,
        error,
        hashlib.sha256(body).hexdigest(),
    )


def _reference_payload(document: FederalRegisterDocument) -> list[JsonObject]:
    return [
        {
            "title": reference.title,
            "part": reference.part,
            "chapter": reference.chapter,
            "citation_url": reference.citation_url,
        }
        for reference in document.cfr_references
    ]


def _source_document(
    document: FederalRegisterDocument,
    references: tuple[JsonObject, ...],
    dockets: tuple[str, ...],
    sections: tuple[ParsedSection, ...],
    parse_error: str | None,
    source_xml_sha256: str | None,
) -> SourceDocument:
    return SourceDocument(
        document_number=document.document_number,
        title=document.title,
        document_type=document.type,
        publication_date=document.publication_date,
        effective_on=document.effective_on,
        citation=document.citation,
        source_url=document.html_url,
        cfr_references=references,
        docket_ids=dockets,
        correction_of=document.correction_of,
        sections=sections,
        source_xml_sha256=source_xml_sha256,
        parse_error=parse_error,
    )


def _write_staging(path: Path, documents: tuple[CuratedDocument, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "document_number": document.document_number,
                "section_ref": section.section_ref,
                "heading": section.heading,
                "text": section.text,
                "position": section.position,
            },
            ensure_ascii=False,
        )
        for document in documents
        for section in document.sections
    ]
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _write_manifest(
    directory: Path,
    documents: tuple[CuratedDocument, ...],
    *,
    query: JsonObject,
    git_sha: str | None,
    staging_path: Path,
) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    body: JsonObject = {
        "source": "federal_register",
        "query": query,
        "document_count": len(documents),
        "section_count": sum(len(document.sections) for document in documents),
        "git_sha": git_sha,
        "documents": [
            {
                "document_number": document.document_number,
                "content_sha256": document.content_sha256,
                "source_xml_sha256": document.source_xml_sha256,
                "section_count": len(document.sections),
            }
            for document in documents
        ],
        "files": [
            {
                "path": staging_path.as_posix(),
                "sha256": hashlib.sha256(staging_path.read_bytes()).hexdigest(),
            }
        ],
    }
    version = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    stored = dict(body)
    stored["dataset_version"] = version
    stored["created_at"] = datetime.now(UTC).isoformat()
    (directory / f"{version}.json").write_text(
        json.dumps(stored, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return version


def _counts(report: QualityReport, stats: LoadStats) -> JsonObject:
    return {
        "documents": report.documents,
        "parse_failures": report.parse_failures,
        "empty_sections_dropped": report.empty_sections_dropped,
        "sections_split": report.sections_split,
        "inserted": stats.inserted,
        "unchanged": stats.unchanged,
        "sections": stats.sections,
    }


def _finish(
    session: Session,
    run: IngestionRun,
    status: IngestionStatus,
    counts: JsonObject,
    dataset_version: str | None,
    error: str | None,
) -> None:
    run.status = status
    run.counts = counts
    run.dataset_version = dataset_version
    run.error = error[:240] if error else None
    run.finished_at = utc_now()
    session.commit()
