import json

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.domain.enums import AnalysisOutcome
from app.domain.schemas import Citation, ModelAnalysis
from app.main import app
from app.services.providers import ModelRequest, OpenAICompatibleProvider
from app.services.retrieval import RetrievedPolicy
from app.services.validation import validate_analysis


def test_oidc_mode_requires_bearer_token(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="production",
        auth_mode="oidc",
        oidc_issuer="https://identity.example.test",
        oidc_audience="compliance-api",
        oidc_jwks_url="https://identity.example.test/.well-known/jwks.json",
    )
    try:
        response = client.get("/api/v1/policies")
    finally:
        app.dependency_overrides.pop(get_settings)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_development_authentication_is_rejected_in_production(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="production",
        auth_mode="development",
    )
    try:
        response = client.get("/api/v1/policies")
    finally:
        app.dependency_overrides.pop(get_settings)

    assert response.status_code == 503


def test_document_size_limit_is_enforced(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(max_document_bytes=1024)
    try:
        response = client.post(
            "/api/v1/documents",
            json={
                "title": "Oversized procedure",
                "source": "reference-test",
                "content": "x" * 1025,
            },
        )
    finally:
        app.dependency_overrides.pop(get_settings)

    assert response.status_code == 413


def test_openai_compatible_provider_requests_strict_structured_output() -> None:
    policy = RetrievedPolicy(
        id="policy-1",
        name="retention",
        version=2,
        content="Customer records must be retained for seven years.",
        score=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "untrusted_document" in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "outcome": "compliant",
                                    "confidence": 0.91,
                                    "rationale": "The procedure matches the cited retention rule.",
                                    "citations": [
                                        {
                                            "policy_id": "policy-1",
                                            "policy_version": 2,
                                            "quote": policy.content,
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as model_client:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example.test/v1",
            api_key="test-only-key",
            model="structured-model",
            client=model_client,
        )
        response = provider.analyze(
            ModelRequest(
                system_prompt="Return a grounded structured compliance assessment.",
                document="This procedure retains customer records for seven years.",
                policies=[policy],
            )
        )

    assert response.analysis.outcome == AnalysisOutcome.COMPLIANT
    assert response.input_tokens == 120
    assert response.output_tokens == 40


def test_validation_rejects_unretrieved_and_unsupported_citations() -> None:
    policy = RetrievedPolicy(
        id="policy-1",
        name="retention",
        version=2,
        content="Customer records must be retained for seven years.",
        score=1,
    )
    analysis = ModelAnalysis(
        outcome=AnalysisOutcome.COMPLIANT,
        confidence=0.92,
        rationale="The model supplied citations that require deterministic verification.",
        citations=[
            Citation(
                policy_id=policy.id,
                policy_version=policy.version,
                quote="Customer records may be deleted immediately.",
            ),
            Citation(
                policy_id="policy-not-retrieved",
                policy_version=1,
                quote="An unrelated requirement.",
            ),
        ],
    )

    errors = validate_analysis(analysis, [policy], confidence_threshold=0.7)

    assert errors == [
        "citation_quote_not_found:policy-1:2",
        "citation_not_retrieved:policy-not-retrieved:1",
    ]


def test_validation_requires_meaningful_word_bounded_quotes() -> None:
    policy = RetrievedPolicy(
        id="policy-1",
        name="retention",
        version=2,
        content="Customer records must be retained for seven years.",
        score=1,
    )
    analysis = ModelAnalysis(
        outcome=AnalysisOutcome.COMPLIANT,
        confidence=0.92,
        rationale="The citations require deterministic verification.",
        citations=[
            Citation(
                policy_id=policy.id,
                policy_version=policy.version,
                quote="Customer",
            ),
            Citation(
                policy_id=policy.id,
                policy_version=policy.version,
                quote="ustomer records must be retained",
            ),
        ],
    )

    errors = validate_analysis(analysis, [policy], confidence_threshold=0.7)

    assert errors == [
        "citation_quote_too_short:policy-1:2",
        "citation_quote_not_found:policy-1:2",
    ]


def test_validation_normalizes_only_whitespace_for_quote_matching() -> None:
    policy = RetrievedPolicy(
        id="policy-1",
        name="retention",
        version=2,
        content="Customer records must be\nretained for seven years.",
        score=1,
    )
    supported = ModelAnalysis(
        outcome=AnalysisOutcome.COMPLIANT,
        confidence=0.92,
        rationale="The citation preserves the source wording.",
        citations=[
            Citation(
                policy_id=policy.id,
                policy_version=policy.version,
                quote="Customer records must be retained for seven years.",
            )
        ],
    )
    changed_case = ModelAnalysis(
        outcome=AnalysisOutcome.COMPLIANT,
        confidence=0.92,
        rationale="The citation changes more than whitespace.",
        citations=[
            Citation(
                policy_id=policy.id,
                policy_version=policy.version,
                quote="customer records must be retained for seven years.",
            )
        ],
    )

    assert validate_analysis(supported, [policy], confidence_threshold=0.7) == []
    assert validate_analysis(changed_case, [policy], confidence_threshold=0.7) == [
        "citation_quote_not_found:policy-1:2"
    ]


def test_validation_requires_temporal_claims_in_cited_passages() -> None:
    policy = RetrievedPolicy(
        id="policy-1",
        name="reporting",
        version=1,
        content=(
            "Reports must be submitted within 30 days after notice. "
            "The rule takes effect on 2026-01-01."
        ),
        score=1,
    )
    citation = Citation(
        policy_id=policy.id,
        policy_version=policy.version,
        quote=policy.content,
    )
    supported = ModelAnalysis(
        outcome=AnalysisOutcome.COMPLIANT,
        confidence=0.92,
        rationale="Reports are due within 30 days and the rule applies on 2026-01-01.",
        citations=[citation],
    )
    unsupported = ModelAnalysis(
        outcome=AnalysisOutcome.COMPLIANT,
        confidence=0.92,
        rationale="Reports are due within 15 days and the rule applies on 2027-01-01.",
        citations=[citation],
    )

    assert validate_analysis(supported, [policy], confidence_threshold=0.7) == []
    assert validate_analysis(unsupported, [policy], confidence_threshold=0.7) == [
        "rationale_temporal_claim_not_cited:within 15 days",
        "rationale_temporal_claim_not_cited:on 2027-01-01",
    ]
