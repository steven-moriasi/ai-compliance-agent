from datetime import UTC, datetime

import httpx
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

    def analyze(self, case: ComplianceCase, correlation_id: str) -> ComplianceCase:
        case.status = CaseStatus.ANALYZING
        case.started_at = datetime.now(UTC)
        self.session.commit()

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

            case.model_provider = self.provider.name
            case.model_name = self.provider.model
            case.outcome = response.analysis.outcome
            case.confidence = response.analysis.confidence
            case.rationale = response.analysis.rationale
            case.citations = [
                citation.model_dump(mode="json") for citation in response.analysis.citations
            ]
            case.validation_errors = errors
            case.injection_signals = injection_signals
            case.input_tokens = response.input_tokens
            case.output_tokens = response.output_tokens
            case.latency_ms = response.latency_ms
            case.estimated_cost_usd = (
                response.input_tokens * self.settings.model_input_cost_per_million
                + response.output_tokens * self.settings.model_output_cost_per_million
            ) / 1_000_000
            case.status = CaseStatus.REVIEW_REQUIRED
            case.completed_at = datetime.now(UTC)
            append_audit_event(
                self.session,
                event_type="analysis_completed",
                actor_id="analysis-worker",
                correlation_id=correlation_id,
                case_id=case.id,
                details={
                    "model_provider": self.provider.name,
                    "model_name": self.provider.model,
                    "validation_error_count": len(errors),
                    "injection_signal_count": len(injection_signals),
                },
            )
        except (httpx.HTTPError, ValueError) as exc:
            case.status = CaseStatus.FAILED
            case.error_code = "model_analysis_failed"
            case.error_message = str(exc)
            case.completed_at = datetime.now(UTC)
            append_audit_event(
                self.session,
                event_type="analysis_failed",
                actor_id="analysis-worker",
                correlation_id=correlation_id,
                case_id=case.id,
                details={"error_code": case.error_code},
            )

        self.session.commit()
        self.session.refresh(case)
        return case
