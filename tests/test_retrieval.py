import hashlib

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.models import Policy
from app.services.retrieval import retrieve_policies


def _policy(policy_id: str, name: str, content: str) -> Policy:
    return Policy(
        id=policy_id,
        name=name,
        version=1,
        status=PolicyStatus.ACTIVE,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        created_by="test",
    )


def test_retrieval_ranks_by_document_token_coverage(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add_all(
            (
                _policy(
                    "policy-reporting",
                    "incident-reporting",
                    (
                        "Reports must be submitted within 30 days after notice. "
                        "The records team documents receipt, escalation, evidence, "
                        "and resolution for every filing."
                    ),
                ),
                _policy("policy-notice", "notice-handling", "Reports follow notice."),
            )
        )
        session.commit()

        results = retrieve_policies(
            session,
            "Reports must be submitted within 30 days after notice.",
        )

    assert [result.id for result in results] == ["policy-reporting", "policy-notice"]
    assert results[0].score == 1
    assert results[1].score == pytest.approx(2 / 7)


def test_retrieval_breaks_equal_scores_by_policy_name(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add_all(
            (
                _policy("policy-zeta", "zeta-policy", "Reports require evidence."),
                _policy("policy-alpha", "alpha-policy", "Reports require evidence."),
            )
        )
        session.commit()

        results = retrieve_policies(session, "Reports require evidence.")

    assert [result.id for result in results] == ["policy-alpha", "policy-zeta"]
