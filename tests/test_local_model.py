import json
import os
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.domain.enums import AnalysisOutcome
from app.services.provider_factory import build_provider
from app.services.providers import DeterministicProvider, ModelRequest, OpenAICompatibleProvider
from app.services.retrieval import RetrievedPolicy
from evals.local_model import local_server_reachable, main, measure_case
from evals.run import load_evaluation_set


def test_ollama_provider_requests_a_json_object() -> None:
    policy = RetrievedPolicy(
        id="policy-1",
        name="retention",
        version=2,
        section_ref="7",
        heading="Retention",
        content="Customer records must be retained for seven years.",
        position=0,
        score=1,
    )
    body = {
        "outcome": "needs_review",
        "confidence": 0.65,
        "rationale": "A human must confirm how the cited policy applies.",
        "citations": [
            {
                "policy_id": "policy-1",
                "policy_version": 2,
                "section_ref": "7",
                "quote": "Customer records must be retained for seven years.",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "json_schema" not in payload["response_format"]
        assert payload["model"] == "qwen2.5:3b"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "```json\n" + json.dumps(body) + "\n```"}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as model_client:
        provider = build_provider(
            Settings(
                model_provider="ollama",
                local_model_base_url="http://ollama.test/v1",
                local_model_name="qwen2.5:3b",
            )
        )
        assert isinstance(provider, OpenAICompatibleProvider)
        provider.client = model_client
        response = provider.analyze(
            ModelRequest(
                system_prompt="Return a grounded structured compliance assessment.",
                document="This procedure retains customer records for seven years.",
                policies=[policy],
            )
        )

    assert provider.name == "ollama"
    assert response.analysis.outcome == AnalysisOutcome.NEEDS_REVIEW


def test_measure_case_records_the_configured_provider() -> None:
    evaluation = load_evaluation_set(Path("evals/cases/v1.json")).cases[0]
    measured = measure_case(evaluation, DeterministicProvider())
    assert measured["case_id"] == evaluation.id
    assert measured["provider"] == "deterministic"
    assert measured["model"] == "local-reference-1"
    assert measured["status"] == "review_required"


def test_missing_local_server_writes_no_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("evals.local_model.local_server_reachable", lambda _base_url: False)
    assert main() == 1
    assert not Path("evals/reports/local_model.json").exists()


def test_unreachable_server_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def _down(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _down)
    assert local_server_reachable("http://127.0.0.1:11434/v1") is False


@pytest.mark.local_model
def test_live_local_model_is_opt_in() -> None:
    if os.environ.get("COMPLIANCE_LOCAL_MODEL") != "1":
        pytest.skip("set COMPLIANCE_LOCAL_MODEL=1 to call a running local model")
    assert local_server_reachable("http://127.0.0.1:11434/v1")
