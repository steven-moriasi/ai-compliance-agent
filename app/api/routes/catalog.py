import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.domain.models import Document, Policy, PolicySection, PromptTemplate
from app.domain.schemas import (
    DocumentCreate,
    DocumentRead,
    PolicyCreate,
    PolicyRead,
    PolicySectionCreate,
    PromptTemplateCreate,
    PromptTemplateRead,
)
from app.infrastructure.auth import AuthContext, require_roles
from app.infrastructure.database import get_session
from app.services.audit import append_audit_event

router = APIRouter(prefix="/api/v1", tags=["catalog"])
AdminContext = Annotated[AuthContext, Depends(require_roles("admin"))]
AnalystContext = Annotated[AuthContext, Depends(require_roles("admin", "analyst"))]
ViewerContext = Annotated[
    AuthContext,
    Depends(require_roles("admin", "analyst", "reviewer", "viewer")),
]
DatabaseSession = Annotated[Session, Depends(get_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]


@router.post("/policies", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyCreate, context: AdminContext, session: DatabaseSession) -> Policy:
    sections = payload.sections or [
        PolicySectionCreate(
            section_ref="document",
            heading=payload.name,
            text=payload.content,
            position=0,
        )
    ]
    policy = Policy(
        **payload.model_dump(exclude={"sections"}),
        content_hash=hashlib.sha256(payload.content.encode()).hexdigest(),
        created_by=context.subject,
        sections=[PolicySection(**section.model_dump()) for section in sections],
    )
    session.add(policy)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Policy name and version already exist"
        ) from exc
    session.refresh(policy)
    return policy


@router.get("/policies", response_model=list[PolicyRead])
def list_policies(_context: ViewerContext, session: DatabaseSession) -> list[Policy]:
    return list(
        session.scalars(
            select(Policy)
            .options(selectinload(Policy.sections))
            .order_by(Policy.name, Policy.version)
        )
    )


@router.post("/prompts", response_model=PromptTemplateRead, status_code=status.HTTP_201_CREATED)
def create_prompt(
    payload: PromptTemplateCreate,
    context: AdminContext,
    session: DatabaseSession,
) -> PromptTemplate:
    if payload.active:
        session.execute(
            update(PromptTemplate).where(PromptTemplate.name == payload.name).values(active=False)
        )
    prompt = PromptTemplate(**payload.model_dump(), created_by=context.subject)
    session.add(prompt)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Prompt name and version already exist"
        ) from exc
    session.refresh(prompt)
    return prompt


@router.get("/prompts", response_model=list[PromptTemplateRead])
def list_prompts(_context: ViewerContext, session: DatabaseSession) -> list[PromptTemplate]:
    return list(
        session.scalars(
            select(PromptTemplate).order_by(PromptTemplate.name, PromptTemplate.version)
        )
    )


@router.post("/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    context: AnalystContext,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> Document:
    if len(payload.content.encode()) > settings.max_document_bytes:
        raise HTTPException(status_code=413, detail="Document exceeds configured size limit")
    document = Document(
        **payload.model_dump(),
        content_hash=hashlib.sha256(payload.content.encode()).hexdigest(),
        created_by=context.subject,
    )
    session.add(document)
    session.flush()
    append_audit_event(
        session,
        event_type="document_ingested",
        actor_id=context.subject,
        correlation_id=str(uuid.uuid4()),
        details={"document_id": document.id, "content_hash": document.content_hash},
    )
    session.commit()
    session.refresh(document)
    return document
