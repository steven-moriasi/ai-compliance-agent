import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import CaseStatus
from app.domain.models import ComplianceCase
from app.services.audit import append_audit_event


class _RowCountResult(Protocol):
    rowcount: int


def claim_next_case(
    session: Session,
    worker_id: str,
    lease_seconds: int,
    correlation_id: str | None = None,
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
    claimed = cast(
        _RowCountResult,
        session.execute(
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
        ),
    )
    if claimed.rowcount != 1:
        session.rollback()
        return None
    case = session.get(ComplianceCase, case_id, populate_existing=True)
    if case is None:
        session.rollback()
        return None
    append_audit_event(
        session,
        event_type="analysis_started",
        actor_id=worker_id,
        correlation_id=correlation_id or str(uuid.uuid4()),
        case_id=case_id,
        details={"attempt": case.attempts, "fencing_token": case.fencing_token},
    )
    session.commit()
    return case


def release_failed_claim(
    session: Session,
    *,
    case_id: str,
    worker_id: str,
    fencing_token: int,
    max_attempts: int,
    correlation_id: str,
    error_type: str,
) -> CaseStatus | None:
    """Hand back a case after an unexpected worker error instead of waiting for the lease.

    The case returns to the queue, or fails once its attempts are used up, the same way the
    reaper treats an expired lease. The fencing guard makes this a no-op for a worker that
    has already lost the case. Only the exception type is recorded, never its message,
    because messages can quote document text.
    """
    session.rollback()
    owned = (
        ComplianceCase.id == case_id,
        ComplianceCase.status == CaseStatus.ANALYZING,
        ComplianceCase.worker_id == worker_id,
        ComplianceCase.fencing_token == fencing_token,
    )
    attempts = session.scalar(select(ComplianceCase.attempts).where(*owned))
    if attempts is None:
        session.rollback()
        return None
    now = datetime.now(UTC)
    if attempts >= max_attempts:
        status = CaseStatus.FAILED
        values: dict[str, object] = {
            "status": status,
            "error_code": "analysis_worker_error",
            "error_message": "Analysis stopped on an unexpected worker error",
            "completed_at": now,
            "lease_expires_at": None,
        }
        event_type = "analysis_abandoned"
    else:
        status = CaseStatus.QUEUED
        values = {"status": status, "worker_id": None, "lease_expires_at": None}
        event_type = "analysis_requeued"
    released = cast(
        _RowCountResult,
        session.execute(update(ComplianceCase).where(*owned).values(**values)),
    )
    if released.rowcount != 1:
        session.rollback()
        return None
    append_audit_event(
        session,
        event_type=event_type,
        actor_id=worker_id,
        correlation_id=correlation_id,
        case_id=case_id,
        details={"attempts": attempts, "fencing_token": fencing_token, "error": error_type},
    )
    session.commit()
    return status


def renew_case_lease(
    session: Session,
    case_id: str,
    worker_id: str,
    fencing_token: int,
    lease_seconds: int,
) -> bool:
    """Extend a live lease. The fencing token stays put so finalisation still matches it.

    Renewal fails once the lease has expired or another worker has claimed the case.
    A new token here would make the worker that still holds the old token unable to finish.
    """
    now = datetime.now(UTC)
    renewed = cast(
        _RowCountResult,
        session.execute(
            update(ComplianceCase)
            .where(
                ComplianceCase.id == case_id,
                ComplianceCase.status == CaseStatus.ANALYZING,
                ComplianceCase.worker_id == worker_id,
                ComplianceCase.fencing_token == fencing_token,
                ComplianceCase.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            .execution_options(synchronize_session=False)
        ),
    )
    session.commit()
    return renewed.rowcount == 1


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
