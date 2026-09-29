import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import PolicyStatus
from app.domain.models import Policy

TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class RetrievedPolicy:
    id: str
    name: str
    version: int
    content: str
    score: float


def _tokens(value: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(value.lower()))


def retrieve_policies(session: Session, document: str, limit: int = 5) -> list[RetrievedPolicy]:
    document_tokens = _tokens(document)
    policies = session.scalars(
        select(Policy).where(Policy.status == PolicyStatus.ACTIVE).order_by(Policy.name)
    )
    ranked: list[RetrievedPolicy] = []
    for policy in policies:
        policy_tokens = _tokens(policy.content)
        overlap = len(document_tokens & policy_tokens)
        score = overlap / max(len(document_tokens), 1)
        if score > 0:
            ranked.append(
                RetrievedPolicy(
                    id=policy.id,
                    name=policy.name,
                    version=policy.version,
                    content=policy.content,
                    score=score,
                )
            )
    return sorted(ranked, key=lambda item: (-item.score, item.name))[:limit]
