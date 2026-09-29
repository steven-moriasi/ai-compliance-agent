import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.domain.enums import CaseStatus, PolicyStatus
from app.domain.models import (
    AuditEvent,
    Base,
    ComplianceCase,
    Document,
    Policy,
    PolicySection,
    PromptTemplate,
)
from app.domain.schemas import ModelAnalysis
from app.domain.types import JsonObject
from app.services.analysis import AnalysisService
from app.services.cases import claim_next_case
from app.services.providers import ModelRequest, ModelResponse


class EvaluationPolicySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ref: str
    heading: str | None = None
    text: str
    position: int = Field(ge=0)


class EvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: int = Field(default=1, ge=1)
    status: PolicyStatus = PolicyStatus.ACTIVE
    content: str
    effective_from: date | None = None
    effective_to: date | None = None
    sections: list[EvaluationPolicySection] = Field(default_factory=list)


class EvaluationExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieved_sources: list[str]
    validation_errors: list[str]
    injection_signals: list[str]
    status: CaseStatus
    error_code: str | None
    provider_attempts: int = Field(ge=0)
    audit_events: list[str]


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document: str
    case_date: date = date(2026, 6, 1)
    policies: list[EvaluationPolicy] = Field(min_length=1)
    provider_outputs: list[JsonObject] = Field(min_length=1)
    expected: EvaluationExpected


class EvaluationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    cases: list[EvaluationCase] = Field(min_length=1)


class FixedOutputProvider:
    name = "evaluation-fixture"
    model = "fixed-output-v1"

    def __init__(self, outputs: list[JsonObject]) -> None:
        self.outputs = outputs
        self.attempts = 0
        self.retrieved_sources: list[str] = []

    def analyze(self, request: ModelRequest) -> ModelResponse:
        self.retrieved_sources = [
            f"{policy.id}:{policy.version}:{policy.section_ref}" for policy in request.policies
        ]
        if self.attempts >= len(self.outputs):
            raise RuntimeError("Evaluation provider received an unexpected extra request")
        output = self.outputs[self.attempts]
        self.attempts += 1
        analysis = ModelAnalysis.model_validate(output)
        return ModelResponse(
            analysis=analysis,
            input_tokens=len(request.document.split())
            + sum(len(policy.content.split()) for policy in request.policies),
            output_tokens=len(json.dumps(output).split()),
            latency_ms=1,
        )


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    expected_retrieval: list[str]
    actual_retrieval: list[str]
    expected_errors: list[str]
    actual_errors: list[str]
    passed: bool


def load_evaluation_set(path: Path) -> EvaluationSet:
    return EvaluationSet.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_case(evaluation: EvaluationCase) -> EvaluationResult:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = FixedOutputProvider(evaluation.provider_outputs)

    try:
        with factory() as session:
            _create_case(session, evaluation)
            claimed = claim_next_case(session, worker_id="evaluation-worker", lease_seconds=120)
            if claimed is None:
                raise RuntimeError(f"Evaluation case {evaluation.id} could not be claimed")

            analyzed = AnalysisService(
                session,
                Settings(confidence_threshold=0.7),
                provider,
            ).analyze(
                claimed,
                correlation_id=f"evaluation-{evaluation.id}",
                worker_id="evaluation-worker",
            )
            audit_events = list(
                session.scalars(
                    select(AuditEvent.event_type)
                    .where(AuditEvent.case_id == analyzed.id)
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                )
            )
            passed = all(
                (
                    provider.retrieved_sources == evaluation.expected.retrieved_sources,
                    analyzed.validation_errors == evaluation.expected.validation_errors,
                    analyzed.injection_signals == evaluation.expected.injection_signals,
                    analyzed.status == evaluation.expected.status,
                    analyzed.error_code == evaluation.expected.error_code,
                    provider.attempts == evaluation.expected.provider_attempts,
                    audit_events == evaluation.expected.audit_events,
                )
            )
            return EvaluationResult(
                case_id=evaluation.id,
                expected_retrieval=evaluation.expected.retrieved_sources,
                actual_retrieval=provider.retrieved_sources,
                expected_errors=evaluation.expected.validation_errors,
                actual_errors=analyzed.validation_errors,
                passed=passed,
            )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _create_case(session: Session, evaluation: EvaluationCase) -> ComplianceCase:
    for fixture in evaluation.policies:
        session.add(
            Policy(
                id=fixture.id,
                name=fixture.name,
                version=fixture.version,
                status=fixture.status,
                content=fixture.content,
                content_hash=hashlib.sha256(fixture.content.encode()).hexdigest(),
                effective_from=fixture.effective_from,
                effective_to=fixture.effective_to,
                created_by="evaluation",
                sections=[PolicySection(**section.model_dump()) for section in fixture.sections],
            )
        )
    prompt = PromptTemplate(
        id=f"{evaluation.id}-prompt",
        name=f"{evaluation.id}-prompt",
        version=1,
        system_prompt="Return a structured analysis grounded only in retrieved policy evidence.",
        output_schema_version="1.0",
        active=True,
        created_by="evaluation",
    )
    document = Document(
        id=f"{evaluation.id}-document",
        title=evaluation.id,
        source="evaluation-fixture",
        content=evaluation.document,
        content_hash=hashlib.sha256(evaluation.document.encode()).hexdigest(),
        created_by="evaluation",
    )
    case = ComplianceCase(
        id=f"{evaluation.id}-case",
        document_id=document.id,
        prompt_template_id=prompt.id,
        idempotency_key=f"evaluation-{evaluation.id}",
        status=CaseStatus.QUEUED,
        requested_by="evaluation",
        created_at=datetime(
            evaluation.case_date.year,
            evaluation.case_date.month,
            evaluation.case_date.day,
            tzinfo=UTC,
        ),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    session.add_all((prompt, document, case))
    session.commit()
    return case


def evaluate_file(path: Path) -> list[EvaluationResult]:
    evaluation_set = load_evaluation_set(path)
    return [evaluate_case(evaluation) for evaluation in evaluation_set.cases]


def render_results(results: Sequence[EvaluationResult]) -> str:
    rows = [
        (
            result.case_id,
            "yes" if result.expected_retrieval == result.actual_retrieval else "no",
            _format_values(result.expected_errors),
            _format_values(result.actual_errors),
            "pass" if result.passed else "fail",
        )
        for result in results
    ]
    headers = ("case", "retrieval hit", "expected errors", "actual errors", "result")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    separator = "-+-".join("-" * width for width in widths)
    lines = [
        " | ".join(value.ljust(widths[index]) for index, value in enumerate(headers)),
        separator,
    ]
    lines.extend(
        " | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in rows
    )
    return "\n".join(lines)


def _format_values(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "-"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic compliance evaluations")
    parser.add_argument("case_file", type=Path)
    arguments = parser.parse_args(argv)
    results = evaluate_file(arguments.case_file)
    print(render_results(results))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
