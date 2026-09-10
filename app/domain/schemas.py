from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AnalysisOutcome, CaseStatus, PolicyStatus, ReviewDecision
from app.domain.types import JsonObject


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    version: int = Field(default=1, ge=1)
    status: PolicyStatus = PolicyStatus.DRAFT
    content: str = Field(min_length=20, max_length=100000)


class PolicyRead(PolicyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content_hash: str
    created_by: str
    created_at: datetime


class PromptTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    version: int = Field(default=1, ge=1)
    system_prompt: str = Field(min_length=20, max_length=20000)
    output_schema_version: str = Field(default="1.0", min_length=1, max_length=40)
    active: bool = False


class PromptTemplateRead(PromptTemplateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_by: str
    created_at: datetime


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=240)
    source: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=20)


class DocumentRead(DocumentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content_hash: str
    created_by: str
    created_at: datetime


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    prompt_template_id: str


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: int
    quote: str = Field(min_length=1, max_length=1000)


class ModelAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: AnalysisOutcome
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=10000)
    citations: list[Citation] = Field(min_length=1, max_length=20)


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    prompt_template_id: str
    idempotency_key: str
    status: CaseStatus
    requested_by: str
    model_provider: str | None
    model_name: str | None
    outcome: AnalysisOutcome | None
    confidence: float | None
    rationale: str | None
    citations: list[dict[str, str | int]]
    validation_errors: list[str]
    injection_signals: list[str]
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    estimated_cost_usd: float | None
    error_code: str | None
    attempts: int
    created_at: datetime
    completed_at: datetime | None


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    rationale: str = Field(min_length=10, max_length=10000)


class ReviewRead(ReviewCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    reviewer_id: str
    analysis_hash: str
    created_at: datetime


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    actor_id: str
    case_id: str | None
    correlation_id: str
    details: JsonObject
    created_at: datetime
