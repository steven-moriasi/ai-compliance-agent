import hashlib
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.models import Policy, PolicySection
from app.services.classifier import (
    cfr_part_drift,
    fit_cfr_prior,
    fit_cfr_prior_from_policies,
    load_cfr_prior,
    predict_cfr_parts,
    save_cfr_prior,
)
from app.services.retrieval import retrieve_policies

CASE_DATE = date(2026, 6, 1)
SHARED = "records must be retained for seven years"


def test_prior_predicts_the_part_whose_words_appear() -> None:
    prior = fit_cfr_prior(
        (
            ("nitrogen oxides stack standard", "40:60"),
            ("ozone implementation transport region", "40:52"),
        )
    )
    predicted = predict_cfr_parts(prior, "nitrogen oxides at the stack")

    assert predicted["40:60"] > predicted["40:52"]


def test_prior_reranks_a_tie_without_recovering_ineligible_policies(
    session_factory: sessionmaker[Session],
) -> None:
    prior = fit_cfr_prior(
        (
            ("nitrogen oxides stack standard", "40:60"),
            ("ozone implementation transport region", "40:52"),
        )
    )
    document = f"{SHARED} nitrogen oxides"
    with session_factory() as session:
        session.add_all(
            (
                _policy(
                    "policy-alpha",
                    "alpha-rule",
                    SHARED,
                    cfr_references=[{"title": 40, "part": "52"}],
                ),
                _policy(
                    "policy-zeta",
                    "zeta-rule",
                    SHARED,
                    cfr_references=[{"title": 40, "part": "60"}],
                ),
                _policy(
                    "policy-draft",
                    "draft-rule",
                    document,
                    status=PolicyStatus.DRAFT,
                    cfr_references=[{"title": 40, "part": "60"}],
                ),
                _policy(
                    "policy-expired",
                    "expired-rule",
                    document,
                    effective_to=date(2026, 1, 1),
                    cfr_references=[{"title": 40, "part": "60"}],
                ),
            )
        )
        session.commit()
        plain = retrieve_policies(session, document, CASE_DATE)
        ranked = retrieve_policies(session, document, CASE_DATE, cfr_prior=prior)

    assert [result.id for result in plain] == ["policy-alpha", "policy-zeta"]
    assert [result.id for result in ranked] == ["policy-zeta", "policy-alpha"]
    assert ranked[0].lexical_score == ranked[1].lexical_score
    assert ranked[0].score > ranked[1].score


def test_drift_flags_a_reversed_part_mix() -> None:
    stable = cfr_part_drift({"40:60": 50, "40:52": 50}, {"40:60": 50, "40:52": 50})
    shifted = cfr_part_drift({"40:60": 90, "40:52": 10}, {"40:60": 10, "40:52": 90})

    assert stable.population_stability == pytest.approx(0)
    assert stable.drifted is False
    assert shifted.drifted is True
    assert shifted.population_stability > shifted.threshold


def test_prior_round_trip_and_policy_fit(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        session.add(
            _policy(
                "policy-sixty",
                "standards",
                "nitrogen oxides stack standard",
                cfr_references=[{"title": 40, "part": "60"}],
                sections=[
                    PolicySection(
                        section_ref="1",
                        heading="Standards",
                        text="nitrogen oxides stack standard",
                        position=0,
                    )
                ],
            )
        )
        session.commit()
        fitted = fit_cfr_prior_from_policies(session)

    path = tmp_path / "cfr_prior.json"
    save_cfr_prior(path, fitted)
    loaded = load_cfr_prior(path)

    assert loaded is not None
    assert predict_cfr_parts(loaded, "nitrogen oxides")["40:60"] == pytest.approx(1)
    assert load_cfr_prior(tmp_path / "missing.json") is None


def _policy(
    policy_id: str,
    name: str,
    content: str,
    *,
    status: PolicyStatus = PolicyStatus.ACTIVE,
    effective_to: date | None = None,
    cfr_references: list[dict[str, int | str]] | None = None,
    sections: list[PolicySection] | None = None,
) -> Policy:
    return Policy(
        id=policy_id,
        name=name,
        version=1,
        status=status,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        effective_to=effective_to,
        cfr_references=cfr_references,
        created_by="test",
        sections=sections or [],
    )
