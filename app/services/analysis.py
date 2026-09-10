from datetime import UTC, datetime

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.enums import CaseStatus
from app.domain.models import ComplianceCase
from app.services.audit import append_audit_event
from app.services.injection import detect_prompt_injection
from app.services.providers import ModelProvider, ModelRequest
from app.services.retrieval import retrieve_policies
from app.services.validation import validate_analysis


class AnalysisService:
    def __init__(self, session: Session, settings: Settings, provider: ModelProvider) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider

    def analyze(
        self,
        case: ComplianceCase,
        correlation_id: str,
        worker_id: str,
    ) -> ComplianceCase:
        policies = retrieve_policies(self.session, case.document.content)
        injection_signals = detect_prompt_injection(case.document.content)
        try:
            response = self.provider.analyze(
                ModelRequest(
                    system_prompt=case.prompt_template.system_prompt,
                    document=case.document.content,
                    policies=policies,
                )
            )
            errors = validate_analysis(
                response.analysis,
                policies,
                self.settings.confidence_threshold,
            )
            if injection_signals:
                errors.append("prompt_injection_signal_requires_review")

            estimated_cost = (
                response.input_tokens * self.settings.model_input_cost_per_million
                + response.output_tokens * self.settings.model_output_cost_per_million
            ) / 1_000_000
            completed = self._finalize(
                case,
                worker_id,
                {
                    "model_provider": self.provider.name,
                    "model_name": self.provider.model,
                    "outcome": response.analysis.outcome,
                    "confidence": response.analysis.confidence,
                    "rationale": response.analysis.rationale,
                    "citations": [
                        citation.model_dump(mode="json")
                        for citation in response.analysis.citations
                    ],
                    "validation_errors": errors,
                    "injection_signals": injection_signals,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "latency_ms": response.latency_ms,
                    "estimated_cost_usd": estimated_cost,
                    "status": CaseStatus.REVIEW_REQUIRED,
                    "completed_at": datetime.now(UTC),
                    "lease_expires_at": None,
                },
            )
            if completed:
                append_audit_event(
                    self.session,
                    event_type="analysis_completed",
                    actor_id=worker_id,
                    correlation_id=correlation_id,
                    case_id=case.id,
                    details={
                        "model_provider": self.provider.name,
                        "model_name": self.provider.model,
                        "validation_error_count": len(errors),
                        "injection_signal_count": len(injection_signals),
                        "fencing_token": case.fencing_token,
                    },
                )
        except (httpx.HTTPError, ValueError):
            completed = self._finalize(
                case,
                worker_id,
                {
                    "status": CaseStatus.FAILED,
                    "error_code": "model_analysis_failed",
                    "error_message": "Provider analysis did not complete",
                    "completed_at": datetime.now(UTC),
                    "lease_expires_at": None,
                },
            )
            if completed:
                append_audit_event(
                    self.session,
                    event_type="analysis_failed",
                    actor_id=worker_id,
                    correlation_id=correlation_id,
                    case_id=case.id,
                    details={
                        "error_code": "model_analysis_failed",
                        "fencing_token": case.fencing_token,
                    },
                )

        self.session.commit()
        self.session.refresh(case)
        return case

    def _finalize(
        self,
        case: ComplianceCase,
        worker_id: str,
        values: dict[str, object],
    ) -> bool:
        finalized = self.session.execute(
            update(ComplianceCase)
            .where(
                ComplianceCase.id == case.id,
                ComplianceCase.status == CaseStatus.ANALYZING,
                ComplianceCase.worker_id == worker_id,
                ComplianceCase.fencing_token == case.fencing_token,
                ComplianceCase.lease_expires_at > datetime.now(UTC),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return finalized.rowcount == 1
