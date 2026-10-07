from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import AnalysisOutcome, CaseStatus, PolicyStatus, ReviewDecision
from app.domain.types import JsonObject


class PolicySectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ref: str = Field(min_length=1, max_length=120)
    heading: str | None = Field(default=None, max_length=240)
    text: str = Field(min_length=1, max_length=100000)
    position: int = Field(ge=0)


class PolicySectionRead(PolicySectionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str


class PolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    version: int = Field(default=1, ge=1)
    status: PolicyStatus = PolicyStatus.DRAFT
    content: str = Field(min_length=20, max_length=100000)
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_effective_window(self) -> Self:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("effective_to must be later than effective_from")
        return self


class PolicyCreate(PolicyBase):
    sections: list[PolicySectionCreate] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        section_refs = [section.section_ref for section in self.sections]
        positions = [section.position for section in self.sections]
        if len(section_refs) != len(set(section_refs)):
            raise ValueError("section_ref values must be unique")
        if len(positions) != len(set(positions)):
            raise ValueError("section positions must be unique")
        return self


class PolicyRead(PolicyBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content_hash: str
    created_by: str
    created_at: datetime
    sections: list[PolicySectionRead]


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
    section_ref: str = Field(min_length=1, max_length=120)
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


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    name: str
    version: int
    section_ref: str
    heading: str | None
    content: str
    score: float
    lexical_score: float
    embedding_score: float


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["keyword", "embedding", "hybrid"]
    as_of: date
    results: list[SearchHit]


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    actor_id: str
    case_id: str | None
    correlation_id: str
    details: JsonObject
    created_at: datetime
