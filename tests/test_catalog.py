from fastapi.testclient import TestClient


def test_policy_catalog_preserves_sections_and_effective_window(client: TestClient) -> None:
    response = client.post(
        "/api/v1/policies",
        json={
            "name": "incident-reporting",
            "version": 2,
            "status": "active",
            "content": "Section 1 defines scope. Section 2 requires reports within 30 days.",
            "effective_from": "2026-01-01",
            "effective_to": "2027-01-01",
            "sections": [
                {
                    "section_ref": "2",
                    "heading": "Reporting",
                    "text": "Reports must be submitted within 30 days.",
                    "position": 1,
                },
                {
                    "section_ref": "1",
                    "heading": "Scope",
                    "text": "This regulation applies to incident reports.",
                    "position": 0,
                },
            ],
        },
    )

    assert response.status_code == 201
    policy = response.json()
    assert policy["effective_from"] == "2026-01-01"
    assert policy["effective_to"] == "2027-01-01"
    assert [section["section_ref"] for section in policy["sections"]] == ["1", "2"]
    assert [section["position"] for section in policy["sections"]] == [0, 1]


def test_policy_without_sections_gets_whole_document_section(client: TestClient) -> None:
    content = "Customer records must be retained for seven years."
    response = client.post(
        "/api/v1/policies",
        json={
            "name": "data-retention",
            "version": 1,
            "status": "active",
            "content": content,
        },
    )

    assert response.status_code == 201
    section = response.json()["sections"][0]
    assert section["section_ref"] == "document"
    assert section["heading"] == "data-retention"
    assert section["text"] == content
    assert section["position"] == 0


def test_policy_rejects_invalid_effective_window_and_duplicate_sections(
    client: TestClient,
) -> None:
    invalid_window = client.post(
        "/api/v1/policies",
        json={
            "name": "invalid-window",
            "status": "active",
            "content": "This policy content is long enough for validation.",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-01",
        },
    )
    duplicate_sections = client.post(
        "/api/v1/policies",
        json={
            "name": "duplicate-sections",
            "status": "active",
            "content": "This policy content is long enough for validation.",
            "sections": [
                {
                    "section_ref": "1",
                    "text": "First section text.",
                    "position": 0,
                },
                {
                    "section_ref": "1",
                    "text": "Second section text.",
                    "position": 1,
                },
            ],
        },
    )

    assert invalid_window.status_code == 422
    assert duplicate_sections.status_code == 422
