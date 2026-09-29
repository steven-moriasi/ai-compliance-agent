from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
from pydantic import JsonValue, ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.enums import AnalysisOutcome, CaseStatus
from app.domain.models import ComplianceCase
from app.domain.types import JsonObject
from app.services.audit import append_audit_event
from app.services.injection import detect_prompt_injection
from app.services.providers import (
    MalformedModelOutputError,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from app.services.retrieval import retrieve_policies
from app.services.validation import validate_analysis

MODEL_FIXABLE_ERRORS = frozenset(
    {
        "citation_not_retrieved",
        "citation_quote_not_found",
        "citation_quote_too_short",
        "rationale_temporal_claim_not_cited",
    }
)


class _RowCountResult(Protocol):
    rowcount: int


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
        if not policies:
            validation_errors = ["no_relevant_source"]
            if injection_signals:
                validation_errors.append("prompt_injection_signal_requires_review")
            completed = self._finalize_no_relevant_source(
                case,
                worker_id,
                validation_errors,
                injection_signals,
            )
            if completed:
                self._append_completion_event(
                    case,
                    worker_id,
                    correlation_id,
                    validation_errors=validation_errors,
                    injection_signals=injection_signals,
                    provider_invoked=False,
                )
            self.session.commit()
            self.session.refresh(case)
            return case

        attempt_events: list[JsonObject] = []
        validation_feedback: tuple[str, ...] = ()
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0

        for attempt in (1, 2):
            try:
                response = self.provider.analyze(
                    ModelRequest(
                        system_prompt=case.prompt_template.system_prompt,
                        document=case.document.content,
                        policies=policies,
                        validation_feedback=validation_feedback,
                    )
                )
            except (MalformedModelOutputError, ValidationError):
                attempt_events.append(
                    self._attempt_event(
                        case,
                        attempt=attempt,
                        result="malformed_output",
                        validation_error_codes=["malformed_model_output"],
                    )
                )
                if attempt == 1:
                    validation_feedback = ("malformed_model_output",)
                    continue
                completed = self._finalize_malformed_output(
                    case,
                    worker_id,
                    injection_signals,
                )
                if completed:
                    self._append_attempt_events(
                        case,
                        worker_id,
                        correlation_id,
                        attempt_events,
                    )
                    self._append_completion_event(
                        case,
                        worker_id,
                        correlation_id,
                        validation_errors=["malformed_model_output"],
                        injection_signals=injection_signals,
                    )
                break
            except (httpx.HTTPError, ValueError):
                attempt_events.append(
                    self._attempt_event(
                        case,
                        attempt=attempt,
                        result="provider_error",
                    )
                )
                completed = self._finalize_provider_failure(case, worker_id)
                if completed:
                    self._append_attempt_events(
                        case,
                        worker_id,
                        correlation_id,
                        attempt_events,
                    )
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
                break

            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            latency_ms += response.latency_ms
            errors = validate_analysis(
                response.analysis,
                policies,
                self.settings.confidence_threshold,
            )
            fixable_errors = _model_fixable_errors(errors)
            attempt_events.append(
                self._attempt_event(
                    case,
                    attempt=attempt,
                    result="validation_failed" if fixable_errors else "valid_output",
                    validation_error_codes=_error_codes(errors),
                    response=response,
                )
            )
            if attempt == 1 and fixable_errors:
                validation_feedback = tuple(fixable_errors)
                continue

            final_errors = [*errors]
            if injection_signals:
                final_errors.append("prompt_injection_signal_requires_review")
            completed = self._finalize_response(
                case,
                worker_id,
                response,
                final_errors,
                injection_signals,
                input_tokens,
                output_tokens,
                latency_ms,
            )
            if completed:
                self._append_attempt_events(
                    case,
                    worker_id,
                    correlation_id,
                    attempt_events,
                )
                self._append_completion_event(
                    case,
                    worker_id,
                    correlation_id,
                    validation_errors=final_errors,
                    injection_signals=injection_signals,
                )
            break

        self.session.commit()
        self.session.refresh(case)
        return case

    def _finalize_response(
        self,
        case: ComplianceCase,
        worker_id: str,
        response: ModelResponse,
        validation_errors: list[str],
        injection_signals: list[str],
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
    ) -> bool:
        estimated_cost = (
            input_tokens * self.settings.model_input_cost_per_million
            + output_tokens * self.settings.model_output_cost_per_million
        ) / 1_000_000
        return self._finalize(
            case,
            worker_id,
            {
                "model_provider": self.provider.name,
                "model_name": self.provider.model,
                "outcome": response.analysis.outcome,
                "confidence": response.analysis.confidence,
                "rationale": response.analysis.rationale,
                "citations": [
                    citation.model_dump(mode="json") for citation in response.analysis.citations
                ],
                "validation_errors": validation_errors,
                "injection_signals": injection_signals,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "estimated_cost_usd": estimated_cost,
                "error_code": None,
                "error_message": None,
                "status": CaseStatus.REVIEW_REQUIRED,
                "completed_at": datetime.now(UTC),
                "lease_expires_at": None,
            },
        )

    def _finalize_malformed_output(
        self,
        case: ComplianceCase,
        worker_id: str,
        injection_signals: list[str],
    ) -> bool:
        return self._finalize(
            case,
            worker_id,
            {
                "model_provider": self.provider.name,
                "model_name": self.provider.model,
                "validation_errors": ["malformed_model_output"],
                "injection_signals": injection_signals,
                "status": CaseStatus.REVIEW_REQUIRED,
                "error_code": "malformed_model_output",
                "error_message": "Model output did not match the required schema",
                "completed_at": datetime.now(UTC),
                "lease_expires_at": None,
            },
        )

    def _finalize_provider_failure(
        self,
        case: ComplianceCase,
        worker_id: str,
    ) -> bool:
        return self._finalize(
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

    def _finalize_no_relevant_source(
        self,
        case: ComplianceCase,
        worker_id: str,
        validation_errors: list[str],
        injection_signals: list[str],
    ) -> bool:
        message = "No relevant active policy source was retrieved"
        return self._finalize(
            case,
            worker_id,
            {
                "outcome": AnalysisOutcome.NEEDS_REVIEW,
                "rationale": message,
                "citations": [],
                "validation_errors": validation_errors,
                "injection_signals": injection_signals,
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": 0,
                "estimated_cost_usd": 0,
                "status": CaseStatus.REVIEW_REQUIRED,
                "error_code": "no_relevant_source",
                "error_message": message,
                "completed_at": datetime.now(UTC),
                "lease_expires_at": None,
            },
        )

    def _attempt_event(
        self,
        case: ComplianceCase,
        *,
        attempt: int,
        result: str,
        validation_error_codes: list[str] | None = None,
        response: ModelResponse | None = None,
    ) -> JsonObject:
        details: JsonObject = {
            "attempt": attempt,
            "model_provider": self.provider.name,
            "model_name": self.provider.model,
            "result": result,
            "validation_error_codes": cast(JsonValue, validation_error_codes or []),
            "fencing_token": case.fencing_token,
        }
        if response is not None:
            details.update(
                {
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "latency_ms": response.latency_ms,
                }
            )
        return details

    def _append_attempt_events(
        self,
        case: ComplianceCase,
        worker_id: str,
        correlation_id: str,
        attempt_events: list[JsonObject],
    ) -> None:
        for details in attempt_events:
            append_audit_event(
                self.session,
                event_type="analysis_attempted",
                actor_id=worker_id,
                correlation_id=correlation_id,
                case_id=case.id,
                details=details,
            )

    def _append_completion_event(
        self,
        case: ComplianceCase,
        worker_id: str,
        correlation_id: str,
        *,
        validation_errors: list[str],
        injection_signals: list[str],
        provider_invoked: bool = True,
    ) -> None:
        append_audit_event(
            self.session,
            event_type="analysis_completed",
            actor_id=worker_id,
            correlation_id=correlation_id,
            case_id=case.id,
            details={
                "model_provider": self.provider.name,
                "model_name": self.provider.model,
                "provider_invoked": provider_invoked,
                "validation_error_count": len(validation_errors),
                "injection_signal_count": len(injection_signals),
                "fencing_token": case.fencing_token,
            },
        )

    def _finalize(
        self,
        case: ComplianceCase,
        worker_id: str,
        values: dict[str, object],
    ) -> bool:
        finalized = cast(
            _RowCountResult,
            self.session.execute(
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
            ),
        )
        return finalized.rowcount == 1


def _model_fixable_errors(errors: list[str]) -> list[str]:
    return list(
        dict.fromkeys(error for error in errors if error.partition(":")[0] in MODEL_FIXABLE_ERRORS)
    )


def _error_codes(errors: list[str]) -> list[str]:
    return list(dict.fromkeys(error.partition(":")[0] for error in errors))
