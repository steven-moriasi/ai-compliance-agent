import hashlib
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.metrics import REVIEWS
from app.domain.enums import CaseStatus, ReviewDecision
from app.domain.models import AuditEvent, ComplianceCase, Document, PromptTemplate, ReviewRecord
from app.domain.schemas import AuditEventRead, CaseCreate, CaseRead, ReviewCreate, ReviewRead
from app.infrastructure.auth import AuthContext, require_roles
from app.infrastructure.database import get_session
from app.services.audit import append_audit_event

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])
AnalystContext = Annotated[AuthContext, Depends(require_roles("admin", "analyst"))]
ReviewerContext = Annotated[AuthContext, Depends(require_roles("admin", "reviewer"))]
ViewerContext = Annotated[
    AuthContext,
    Depends(require_roles("admin", "analyst", "reviewer", "viewer")),
]
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.post("", response_model=CaseRead, status_code=status.HTTP_202_ACCEPTED)
def create_case(
    payload: CaseCreate,
    context: AnalystContext,
    session: DatabaseSession,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="X-Idempotency-Key", min_length=8, max_length=160)
    ],
) -> ComplianceCase:
    existing = session.scalar(
        select(ComplianceCase).where(
            ComplianceCase.document_id == payload.document_id,
            ComplianceCase.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        response.headers["Idempotent-Replay"] = "true"
        return existing
    document = session.get(Document, payload.document_id)
    prompt = session.get(PromptTemplate, payload.prompt_template_id)
    if document is None or prompt is None:
        raise HTTPException(status_code=404, detail="Document or prompt template not found")
    if not prompt.active:
        raise HTTPException(status_code=409, detail="Prompt template must be active")

    case = ComplianceCase(
        **payload.model_dump(),
        idempotency_key=idempotency_key,
        requested_by=context.subject,
    )
    session.add(case)
    try:
        session.flush()
        append_audit_event(
            session,
            event_type="case_requested",
            actor_id=context.subject,
            correlation_id=str(uuid.uuid4()),
            case_id=case.id,
            details={"document_id": document.id, "prompt_template_id": prompt.id},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        replay = session.scalar(
            select(ComplianceCase).where(
                ComplianceCase.document_id == payload.document_id,
                ComplianceCase.idempotency_key == idempotency_key,
            )
        )
        if replay is None:
            raise
        response.headers["Idempotent-Replay"] = "true"
        return replay
    session.refresh(case)
    return case


@router.get("/{case_id}", response_model=CaseRead)
def get_case(
    case_id: str,
    _context: ViewerContext,
    session: DatabaseSession,
) -> ComplianceCase:
    case = session.get(ComplianceCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Compliance case not found")
    return case


@router.post("/{case_id}/reviews", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
def review_case(
    case_id: str,
    payload: ReviewCreate,
    context: ReviewerContext,
    session: DatabaseSession,
) -> ReviewRecord:
    case = session.scalar(
        select(ComplianceCase).where(ComplianceCase.id == case_id).with_for_update()
    )
    if case is None:
        raise HTTPException(status_code=404, detail="Compliance case not found")
    if case.status != CaseStatus.REVIEW_REQUIRED:
        raise HTTPException(status_code=409, detail="Case is not awaiting review")
    analysis_snapshot = json.dumps(
        {
            "outcome": case.outcome,
            "confidence": case.confidence,
            "rationale": case.rationale,
            "citations": case.citations,
            "validation_errors": case.validation_errors,
        },
        sort_keys=True,
    )
    review = ReviewRecord(
        case_id=case.id,
        reviewer_id=context.subject,
        decision=payload.decision,
        rationale=payload.rationale,
        analysis_hash=hashlib.sha256(analysis_snapshot.encode()).hexdigest(),
    )
    case.status = (
        CaseStatus.APPROVED
        if payload.decision == ReviewDecision.APPROVE
        else CaseStatus.REJECTED
    )
    session.add(review)
    append_audit_event(
        session,
        event_type="case_reviewed",
        actor_id=context.subject,
        correlation_id=str(uuid.uuid4()),
        case_id=case.id,
        details={"decision": payload.decision.value, "analysis_hash": review.analysis_hash},
    )
    session.commit()
    session.refresh(review)
    REVIEWS.labels(decision=payload.decision.value).inc()
    return review


@router.get("/{case_id}/audit", response_model=list[AuditEventRead])
def list_case_audit(
    case_id: str,
    _context: ViewerContext,
    session: DatabaseSession,
) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.case_id == case_id)
            .order_by(AuditEvent.created_at)
        )
    )
