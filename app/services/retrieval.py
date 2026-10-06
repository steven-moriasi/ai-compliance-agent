import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import PolicyStatus
from app.domain.models import Policy, PolicySection
from app.ingestion.federal_register.synonyms import expand_tokens

TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class RetrievedPolicy:
    id: str
    name: str
    version: int
    section_ref: str
    heading: str | None
    content: str
    position: int
    score: float


def _tokens(value: str) -> set[str]:
    """Token overlap stays lexical. Listed abbreviations add tokens only when a form is present."""
    return expand_tokens(value, set(TOKEN_PATTERN.findall(value.lower())))


def retrieve_policies(
    session: Session,
    document: str,
    case_date: date,
    limit: int = 5,
) -> list[RetrievedPolicy]:
    document_tokens = _tokens(document)
    policies = session.scalars(
        select(Policy)
        .options(selectinload(Policy.sections))
        .where(
            Policy.status == PolicyStatus.ACTIVE,
            or_(Policy.effective_from.is_(None), Policy.effective_from <= case_date),
            or_(Policy.effective_to.is_(None), Policy.effective_to > case_date),
        )
        .order_by(Policy.name, Policy.version)
    )
    ranked: list[RetrievedPolicy] = []
    for policy in policies:
        sections: list[PolicySection | _WholeDocumentSection]
        if policy.sections:
            sections = [*policy.sections]
        else:
            sections = [
                _WholeDocumentSection(
                    section_ref="document",
                    heading=policy.name,
                    text=policy.content,
                    position=0,
                )
            ]
        for section in sections:
            section_tokens = _tokens(" ".join(filter(None, (section.heading, section.text))))
            overlap = len(document_tokens & section_tokens)
            score = overlap / max(len(document_tokens), 1)
            if score > 0:
                ranked.append(
                    RetrievedPolicy(
                        id=policy.id,
                        name=policy.name,
                        version=policy.version,
                        section_ref=section.section_ref,
                        heading=section.heading,
                        content=section.text,
                        position=section.position,
                        score=score,
                    )
                )
    return sorted(
        ranked,
        key=lambda item: (
            -item.score,
            item.name,
            -item.version,
            item.position,
            item.section_ref,
        ),
    )[:limit]


@dataclass(frozen=True)
class _WholeDocumentSection:
    section_ref: str
    heading: str | None
    text: str
    position: int
