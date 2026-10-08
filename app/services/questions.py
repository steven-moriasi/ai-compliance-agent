"""Queue operations for policy questions.

Questions share the worker and the lease rules with cases: a claim bumps the fencing token,
a heartbeat renews the lease, and the reaper requeues a question whose worker went away.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import QuestionStatus
from app.domain.models import PolicyQuestion
from app.services.audit import append_audit_event


class _RowCountResult(Protocol):
    rowcount: int


def claim_next_question(
    session: Session,
    worker_id: str,
    lease_seconds: int,
    correlation_id: str,
) -> PolicyQuestion | None:
    candidate = (
        select(PolicyQuestion.id)
        .where(PolicyQuestion.status == QuestionStatus.QUEUED)
        .order_by(PolicyQuestion.created_at)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        candidate = candidate.with_for_update(skip_locked=True)
    question_id = session.scalar(candidate)
    if question_id is None:
        session.rollback()
        return None
    now = datetime.now(UTC)
    claimed = cast(
        _RowCountResult,
        session.execute(
            update(PolicyQuestion)
            .where(
                PolicyQuestion.id == question_id,
                PolicyQuestion.status == QuestionStatus.QUEUED,
            )
            .values(
                status=QuestionStatus.ANSWERING,
                started_at=now,
                worker_id=worker_id,
                fencing_token=PolicyQuestion.fencing_token + 1,
                attempts=PolicyQuestion.attempts + 1,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        ),
    )
    if claimed.rowcount != 1:
        session.rollback()
        return None
    question = session.get(PolicyQuestion, question_id, populate_existing=True)
    if question is None:
        session.rollback()
        return None
    append_audit_event(
        session,
        event_type="question_started",
        actor_id=worker_id,
        correlation_id=correlation_id,
        details={
            "question_id": question_id,
            "attempt": question.attempts,
            "fencing_token": question.fencing_token,
        },
    )
    session.commit()
    return question


def renew_question_lease(
    session: Session,
    question_id: str,
    worker_id: str,
    fencing_token: int,
    lease_seconds: int,
) -> bool:
    now = datetime.now(UTC)
    renewed = cast(
        _RowCountResult,
        session.execute(
            update(PolicyQuestion)
            .where(
                PolicyQuestion.id == question_id,
                PolicyQuestion.status == QuestionStatus.ANSWERING,
                PolicyQuestion.worker_id == worker_id,
                PolicyQuestion.fencing_token == fencing_token,
                PolicyQuestion.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            .execution_options(synchronize_session=False)
        ),
    )
    session.commit()
    return renewed.rowcount == 1


def release_failed_question(
    session: Session,
    *,
    question_id: str,
    worker_id: str,
    fencing_token: int,
    max_attempts: int,
    correlation_id: str,
    error_type: str,
) -> QuestionStatus | None:
    """Requeue after an unexpected worker error, or fail once the attempts are used up."""
    session.rollback()
    owned = (
        PolicyQuestion.id == question_id,
        PolicyQuestion.status == QuestionStatus.ANSWERING,
        PolicyQuestion.worker_id == worker_id,
        PolicyQuestion.fencing_token == fencing_token,
    )
    attempts = session.scalar(select(PolicyQuestion.attempts).where(*owned))
    if attempts is None:
        session.rollback()
        return None
    status = _release_values(attempts, max_attempts)
    released = cast(
        _RowCountResult,
        session.execute(update(PolicyQuestion).where(*owned).values(**status)),
    )
    if released.rowcount != 1:
        session.rollback()
        return None
    append_audit_event(
        session,
        event_type=_release_event(status),
        actor_id=worker_id,
        correlation_id=correlation_id,
        details={
            "question_id": question_id,
            "attempts": attempts,
            "fencing_token": fencing_token,
            "error": error_type,
        },
    )
    session.commit()
    return cast(QuestionStatus, status["status"])


def reap_expired_questions(
    session: Session,
    max_attempts: int,
    correlation_id: str,
) -> tuple[int, int]:
    now = datetime.now(UTC)
    expired = list(
        session.scalars(
            select(PolicyQuestion).where(
                PolicyQuestion.status == QuestionStatus.ANSWERING,
                PolicyQuestion.lease_expires_at <= now,
            )
        )
    )
    requeued = 0
    failed = 0
    for question in expired:
        values = _release_values(question.attempts, max_attempts)
        for field, value in values.items():
            setattr(question, field, value)
        if values["status"] == QuestionStatus.FAILED:
            failed += 1
        else:
            requeued += 1
        append_audit_event(
            session,
            event_type=_release_event(values),
            actor_id="analysis-reaper",
            correlation_id=correlation_id,
            details={
                "question_id": question.id,
                "attempts": question.attempts,
                "fencing_token": question.fencing_token,
            },
        )
    session.commit()
    return requeued, failed


def _release_values(attempts: int, max_attempts: int) -> dict[str, object]:
    if attempts >= max_attempts:
        return {
            "status": QuestionStatus.FAILED,
            "error_code": "question_attempts_exhausted",
            "completed_at": datetime.now(UTC),
            "lease_expires_at": None,
        }
    return {"status": QuestionStatus.QUEUED, "worker_id": None, "lease_expires_at": None}


def _release_event(values: dict[str, object]) -> str:
    return (
        "question_abandoned" if values["status"] == QuestionStatus.FAILED else "question_requeued"
    )
