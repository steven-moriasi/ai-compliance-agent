import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import (
    AnalysisOutcome,
    CaseStatus,
    IngestionStatus,
    NotificationStatus,
    PolicyStatus,
    ReviewDecision,
)
from app.domain.types import JsonObject


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        Index("ix_policy_name_version", "name", "version", unique=True),
        Index("ix_policy_document_number", "document_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[PolicyStatus] = mapped_column(
        Enum(PolicyStatus, native_enum=False), default=PolicyStatus.DRAFT
    )
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    citation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cfr_references: Mapped[list[JsonObject] | None] = mapped_column(JSON, nullable=True)
    docket_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    effective_date_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correction_of: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sections: Mapped[list["PolicySection"]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="PolicySection.position",
    )


class PolicySection(Base):
    __tablename__ = "policy_sections"
    __table_args__ = (
        Index("ix_policy_section_ref_unique", "policy_id", "section_ref", unique=True),
        Index("ix_policy_section_position_unique", "policy_id", "position", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("policies.id", ondelete="CASCADE"),
        index=True,
    )
    section_ref: Mapped[str] = mapped_column(String(120))
    heading: Mapped[str | None] = mapped_column(String(240), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)

    policy: Mapped[Policy] = relationship(back_populates="sections")


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (Index("ix_prompt_name_version", "name", "version", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text)
    output_schema_version: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240))
    source: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ComplianceCase(Base):
    __tablename__ = "compliance_cases"
    __table_args__ = (
        Index("ix_case_document_idempotency", "document_id", "idempotency_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    prompt_template_id: Mapped[str] = mapped_column(ForeignKey("prompt_templates.id"))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, native_enum=False), default=CaseStatus.QUEUED, index=True
    )
    requested_by: Mapped[str] = mapped_column(String(160))
    model_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    outcome: Mapped[AnalysisOutcome | None] = mapped_column(
        Enum(AnalysisOutcome, native_enum=False), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list[dict[str, str | int]]] = mapped_column(JSON, default=list)
    validation_errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    injection_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    redaction_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[Document] = relationship()
    prompt_template: Mapped[PromptTemplate] = relationship()


class ReviewRecord(Base):
    __tablename__ = "review_records"
    __table_args__ = (Index("ix_review_records_case_unique", "case_id", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("compliance_cases.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(String(160))
    decision: Mapped[ReviewDecision] = mapped_column(Enum(ReviewDecision, native_enum=False))
    rationale: Mapped[str] = mapped_column(Text)
    analysis_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor_id: Mapped[str] = mapped_column(String(160))
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("compliance_cases.id"), nullable=True, index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(160), index=True)
    details: Mapped[JsonObject] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("compliance_cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(120))
    payload: Mapped[JsonObject] = mapped_column(JSON)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, native_enum=False),
        default=NotificationStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingestion_runs_status", "status"),)

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    params: Mapped[JsonObject] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counts: Mapped[JsonObject] = mapped_column(JSON)
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, native_enum=False), default=IngestionStatus.RUNNING
    )
    dataset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(240), nullable=True)
