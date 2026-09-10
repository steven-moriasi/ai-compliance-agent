from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import CaseStatus
from app.domain.models import ComplianceCase
from app.services.audit import append_audit_event


def claim_next_case(
    session: Session,
    worker_id: str,
    lease_seconds: int,
) -> ComplianceCase | None:
    candidate = (
        select(ComplianceCase.id)
        .where(ComplianceCase.status == CaseStatus.QUEUED)
        .order_by(ComplianceCase.created_at)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        candidate = candidate.with_for_update(skip_locked=True)
    case_id = session.scalar(candidate)
    if case_id is None:
        return None
    now = datetime.now(UTC)
    claimed = session.execute(
        update(ComplianceCase)
        .where(
            ComplianceCase.id == case_id,
            ComplianceCase.status == CaseStatus.QUEUED,
        )
        .values(
            status=CaseStatus.ANALYZING,
            started_at=now,
            worker_id=worker_id,
            fencing_token=ComplianceCase.fencing_token + 1,
            attempts=ComplianceCase.attempts + 1,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
    )
    session.commit()
    if claimed.rowcount != 1:
        return None
    return session.get(ComplianceCase, case_id)


def reap_expired_cases(
    session: Session,
    max_attempts: int,
    correlation_id: str,
) -> tuple[int, int]:
    now = datetime.now(UTC)
    expired = list(
        session.scalars(
            select(ComplianceCase).where(
                ComplianceCase.status == CaseStatus.ANALYZING,
                ComplianceCase.lease_expires_at <= now,
            )
        )
    )
    requeued = 0
    failed = 0
    for case in expired:
        if case.attempts >= max_attempts:
            case.status = CaseStatus.FAILED
            case.error_code = "analysis_attempts_exhausted"
            case.error_message = "Analysis lease expired after the maximum number of attempts"
            case.completed_at = now
            failed += 1
            event_type = "analysis_abandoned"
        else:
            case.status = CaseStatus.QUEUED
            case.worker_id = None
            case.lease_expires_at = None
            requeued += 1
            event_type = "analysis_requeued"
        append_audit_event(
            session,
            event_type=event_type,
            actor_id="analysis-reaper",
            correlation_id=correlation_id,
            case_id=case.id,
            details={"attempts": case.attempts, "fencing_token": case.fencing_token},
        )
    session.commit()
    return requeued, failed
