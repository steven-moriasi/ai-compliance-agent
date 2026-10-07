import hashlib
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.models import Policy
from app.services.retrieval import retrieve_policies
from evals.keyword_baseline import KeywordBaseline


def test_keyword_baseline_matches_the_production_scorer(
    session_factory: sessionmaker[Session],
) -> None:
    nox = "The stack limit covers nitrogen oxides at the source every hour."
    other = "This document is about quarterly filing and nothing chemical."
    with session_factory() as session:
        session.add_all(
            [
                _policy("policy-nox", "nox-limit", nox),
                _policy("policy-other", "filing", other),
            ]
        )
        session.commit()
        cached = KeywordBaseline(session).search("NOx", date(2026, 6, 1), 5, {})
        live = retrieve_policies(session, "NOx", date(2026, 6, 1), 5, mode="keyword")
    assert [item.section_ref for item in cached] == [item.section_ref for item in live]
    assert [item.id for item in cached] == ["policy-nox"]


def _policy(policy_id: str, name: str, content: str) -> Policy:
    return Policy(
        id=policy_id,
        name=name,
        version=1,
        status=PolicyStatus.ACTIVE,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        created_by="test",
        sections=[],
    )
