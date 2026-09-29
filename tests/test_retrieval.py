import hashlib
from datetime import date

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.models import Policy, PolicySection
from app.services.retrieval import retrieve_policies


def _policy(
    policy_id: str,
    name: str,
    content: str,
    *,
    version: int = 1,
    effective_from: date | None = None,
    effective_to: date | None = None,
    sections: list[PolicySection] | None = None,
) -> Policy:
    return Policy(
        id=policy_id,
        name=name,
        version=version,
        status=PolicyStatus.ACTIVE,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        effective_from=effective_from,
        effective_to=effective_to,
        created_by="test",
        sections=sections or [],
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
            date(2026, 6, 1),
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

        results = retrieve_policies(
            session,
            "Reports require evidence.",
            date(2026, 6, 1),
        )

    assert [result.id for result in results] == ["policy-alpha", "policy-zeta"]


def test_retrieval_ranks_sections_from_versions_in_force_on_case_date(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add_all(
            (
                _policy(
                    "policy-reporting-v1",
                    "incident-reporting",
                    "Earlier incident reporting rule.",
                    version=1,
                    effective_to=date(2026, 1, 1),
                    sections=[
                        PolicySection(
                            section_ref="4",
                            heading="Reporting",
                            text="Reports must be submitted within 10 days.",
                            position=0,
                        )
                    ],
                ),
                _policy(
                    "policy-reporting-v2",
                    "incident-reporting",
                    "Current incident reporting rule.",
                    version=2,
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2027, 1, 1),
                    sections=[
                        PolicySection(
                            section_ref="1",
                            heading="Scope",
                            text="This regulation applies to regulated organizations.",
                            position=0,
                        ),
                        PolicySection(
                            section_ref="7",
                            heading="Reporting",
                            text="Reports must be submitted within 30 days.",
                            position=1,
                        ),
                    ],
                ),
                _policy(
                    "policy-reporting-v3",
                    "incident-reporting",
                    "Future incident reporting rule.",
                    version=3,
                    effective_from=date(2027, 1, 1),
                    sections=[
                        PolicySection(
                            section_ref="8",
                            heading="Reporting",
                            text="Reports must be submitted within 60 days.",
                            position=0,
                        )
                    ],
                ),
            )
        )
        session.commit()

        results = retrieve_policies(
            session,
            "Reports must be submitted within 30 days.",
            date(2026, 6, 1),
        )

    assert [(result.id, result.version, result.section_ref) for result in results] == [
        ("policy-reporting-v2", 2, "7")
    ]
