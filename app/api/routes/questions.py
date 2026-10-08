from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.domain.models import PolicyQuestion
from app.domain.schemas import QuestionCreate, QuestionRead
from app.infrastructure.auth import AuthContext, require_roles
from app.infrastructure.database import get_session
from app.services.audit import append_audit_event

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])
ViewerContext = Annotated[
    AuthContext,
    Depends(require_roles("admin", "analyst", "reviewer", "viewer")),
]
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.post("", response_model=QuestionRead, status_code=status.HTTP_202_ACCEPTED)
def ask_question(
    payload: QuestionCreate,
    context: ViewerContext,
    session: DatabaseSession,
) -> PolicyQuestion:
    """Queue a question. The worker answers it from policies in force on `as_of`."""
    question = PolicyQuestion(
        question=payload.question.strip(),
        as_of=payload.as_of or date.today(),
        requested_by=context.subject,
    )
    session.add(question)
    session.flush()
    append_audit_event(
        session,
        event_type="question_asked",
        actor_id=context.subject,
        correlation_id=question.id,
        details={"question_id": question.id, "as_of": question.as_of.isoformat()},
    )
    session.commit()
    session.refresh(question)
    return question


@router.get("/{question_id}", response_model=QuestionRead)
def read_question(
    question_id: str,
    context: ViewerContext,
    session: DatabaseSession,
) -> PolicyQuestion:
    question = session.get(PolicyQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    if question.requested_by != context.subject and "admin" not in context.roles:
        raise HTTPException(status_code=404, detail="Question not found")
    return question
