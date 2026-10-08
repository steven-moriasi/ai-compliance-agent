from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import Policy


def test_demo_page_offers_search_cases_and_review(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    assert "Search policy sections" in page
    assert 'id="question-form"' in page
    assert "server default" in page
    assert "Searching…" in page
    assert 'id="case-form"' in page
    assert 'id="review-form"' in page
    assert 'id="audit-log"' in page
    assert "Send a message" not in page


def test_corpus_status_counts_loaded_policies(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    created = client.post(
        "/api/v1/policies",
        json={
            "name": "demo-rule",
            "status": "active",
            "content": "Nitrogen oxides at the stack remain regulated.",
            "sections": [
                {
                    "section_ref": "1",
                    "text": "Nitrogen oxides at the stack remain regulated.",
                    "position": 0,
                }
            ],
        },
    )
    assert created.status_code == 201
    with session_factory() as session:
        policy = session.get(Policy, created.json()["id"])
        assert policy is not None
        policy.publication_date = date(2024, 1, 4)
        session.commit()

    response = client.get("/api/v1/corpus")
    assert response.status_code == 200
    body = response.json()
    assert body["policies"] == 1
    assert body["active_policies"] == 1
    assert body["sections"] == 1
    assert body["dataset_version"] is None
    assert body["publication_date_min"] == "2024-01-04"
    assert body["publication_date_max"] == "2024-01-04"
