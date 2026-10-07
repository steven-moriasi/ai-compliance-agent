import importlib.util
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.routes.search import get_embedder
from app.main import app
from app.services.embeddings import (
    RetrievalUnavailable,
    cosine,
    embedding_model_id,
    sentence_transformer_embedder,
)


def test_keyword_search_returns_mode_as_of_and_score(client: TestClient) -> None:
    created = client.post(
        "/api/v1/policies",
        json={
            "name": "stack-rule",
            "status": "active",
            "content": "Nitrogen oxides at the stack remain regulated.",
            "sections": [
                {
                    "section_ref": "1",
                    "heading": "Limit",
                    "text": "Nitrogen oxides at the stack remain regulated.",
                    "position": 0,
                }
            ],
        },
    )
    assert created.status_code == 201

    response = client.get(
        "/api/v1/search",
        params={"q": "nitrogen oxides at the stack", "as_of": "2026-06-01", "mode": "keyword"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "keyword"
    assert body["as_of"] == "2026-06-01"
    assert body["results"][0]["section_ref"] == "1"
    assert body["results"][0]["score"] == pytest.approx(1)
    assert body["results"][0]["lexical_score"] == pytest.approx(1)


def test_search_skips_rules_not_yet_effective(client: TestClient) -> None:
    created = client.post(
        "/api/v1/policies",
        json={
            "name": "future-rule",
            "status": "active",
            "content": "Future nitrogen oxides limits apply after the effective date.",
            "effective_from": "2027-01-01",
        },
    )
    assert created.status_code == 201
    response = client.get(
        "/api/v1/search",
        params={"q": "nitrogen oxides limits", "as_of": "2026-06-01"},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_hybrid_search_without_a_model_is_unavailable(client: TestClient) -> None:
    if importlib.util.find_spec("sentence_transformers") is not None:
        pytest.skip("optional retrieval extra is installed")
    response = client.get("/api/v1/search", params={"q": "nitrogen oxides", "mode": "hybrid"})
    assert response.status_code == 503


def test_hybrid_search_uses_an_injected_embedder(
    client: TestClient,
) -> None:
    lexical = "Inventory covers nitrogen oxides only in the filing."
    semantic = "That pollutant remains regulated at the stack."
    for name, text in (("lexical-rule", lexical), ("semantic-rule", semantic)):
        created = client.post(
            "/api/v1/policies",
            json={
                "name": name,
                "status": "active",
                "content": text,
                "sections": [{"section_ref": "1", "text": text, "position": 0}],
            },
        )
        assert created.status_code == 201

    class _MappedEmbedder:
        def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
            vectors = {
                "oxides of nitrogen from the stack": (1.0, 0.0),
                lexical: (0.0, 1.0),
                semantic: (1.0, 0.0),
            }
            return [vectors[text] for text in texts]

    app.dependency_overrides[get_embedder] = lambda: _MappedEmbedder()
    try:
        response = client.get(
            "/api/v1/search",
            params={
                "q": "oxides of nitrogen from the stack",
                "as_of": "2026-06-01",
                "mode": "hybrid",
            },
        )
    finally:
        app.dependency_overrides.pop(get_embedder, None)

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["results"]] == [
        "semantic-rule",
        "lexical-rule",
    ]


def test_embedding_identity_keeps_the_revision_and_cosine_is_ranked() -> None:
    assert embedding_model_id("sentence-transformers/all-MiniLM-L6-v2", "abc") == (
        "sentence-transformers/all-MiniLM-L6-v2@abc"
    )
    assert cosine((1.0, 0.0), (1.0, 0.0)) == 1.0
    assert cosine((1.0, 0.0), (0.0, 1.0)) == 0.0


def test_missing_sentence_transformers_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RetrievalUnavailable, match="retrieval extra"):
        sentence_transformer_embedder("sentence-transformers/all-MiniLM-L6-v2")


def test_embedder_reports_truncation_and_float_vectors() -> None:
    from app.services.embeddings import SentenceTransformerEmbedder

    class _Model:
        max_seq_length = 4

        def encode(self, texts: list[str], **_kwargs: object) -> list[list[float]]:
            return [[1.0, 0.0] for _text in texts]

        def tokenizer(self, texts: list[str], **_kwargs: object) -> dict[str, list[list[int]]]:
            return {"input_ids": [[1, 2, 3, 4, 5], [1, 2]]}

    embedder = SentenceTransformerEmbedder(_Model())
    assert embedder.truncated(["long", "short"]) == 1
    assert embedder.embed(["query"]) == [[1.0, 0.0]]
    assert embedder.max_seq_length == 4


def test_search_as_of_defaults_are_not_required_for_keyword(client: TestClient) -> None:
    response = client.get(
        "/api/v1/search",
        params={"q": "nitrogen oxides", "as_of": date.today().isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "keyword"
