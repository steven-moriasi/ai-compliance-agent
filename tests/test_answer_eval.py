from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domain.models import Policy
from app.services.providers import DeterministicProvider
from evals.answer_eval import AnswerItem, evaluate, load_items


def test_the_versioned_question_set_loads() -> None:
    items = load_items(Path("evals/answers/questions_v1.jsonl"))
    assert {item.category for item in items} == {"in-scope", "not-in-force", "out-of-scope"}
    assert len({item.id for item in items}) == len(items)
    for item in items:
        if item.category == "in-scope":
            assert item.expected_document_numbers
        if item.category == "not-in-force":
            assert item.forbidden_document_numbers


def test_evaluation_scores_each_category(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    text = "Customer records must be retained for seven years after the account closes."
    created = client.post(
        "/api/v1/policies",
        json={
            "name": "data-retention",
            "status": "active",
            "content": text,
            "effective_from": "2024-01-01",
            "sections": [{"section_ref": "4.2", "text": text, "position": 0}],
        },
    )
    assert created.status_code == 201
    with session_factory() as session:
        policy = session.get(Policy, created.json()["id"])
        assert policy is not None
        policy.document_number = "2024-00001"
        session.commit()
    items = [
        AnswerItem(
            "retention",
            "in-scope",
            "How long are customer records retained?",
            date(2026, 1, 1),
            ("2024-00001",),
            (),
        ),
        AnswerItem(
            "retention-early",
            "not-in-force",
            "How long are customer records retained?",
            date(2023, 12, 31),
            (),
            ("2024-00001",),
        ),
        AnswerItem(
            "cake",
            "out-of-scope",
            "What is a good chocolate cake recipe?",
            date(2026, 1, 1),
            (),
            (),
        ),
    ]

    with session_factory() as session:
        result = evaluate(session, items, Settings(), DeterministicProvider())

    rows = {row["id"]: row for row in result["items"]}
    assert rows["retention"]["passed"] is True
    assert rows["retention"]["cited_document_numbers"] == ["2024-00001"]
    assert rows["retention-early"]["status"] == "unanswered"
    assert rows["retention-early"]["passed"] is True
    assert rows["cake"]["passed"] is True
    summary = result["summary"]
    assert summary["in-scope"] == {"items": 1, "answered": 1, "passed": 1, "pass_rate": 1.0}
    assert summary["unanswered_reasons"] == {"no_relevant_source": 2}
