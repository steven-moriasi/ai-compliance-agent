import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domain.enums import QuestionStatus
from app.domain.models import AuditEvent, Policy, PolicyQuestion
from app.domain.schemas import AnswerCitation, ModelAnswer
from app.services.provider_factory import build_provider
from app.services.providers import (
    AnswerRequest,
    AnswerResponse,
    DeterministicProvider,
    MalformedModelOutputError,
    OpenAICompatibleProvider,
)
from app.services.questions import claim_next_question, reap_expired_questions
from app.services.retrieval import RetrievedPolicy
from app.services.validation import validate_answer
from app.worker import process_next_question

RETENTION = (
    "Customer records must be retained for seven years. "
    "Access tokens must not be written to operational documents."
)


class ScriptedProvider:
    """Returns prepared answers in order and keeps every request it was sent."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *answers: ModelAnswer | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[AnswerRequest] = []

    def answer(self, request: AnswerRequest) -> AnswerResponse:
        self.requests.append(request)
        prepared = self.answers.pop(0)
        if isinstance(prepared, Exception):
            raise prepared
        return AnswerResponse(answer=prepared, input_tokens=100, output_tokens=20, latency_ms=50)


def _policy(client: TestClient, content: str = RETENTION, name: str = "data-retention") -> str:
    created = client.post(
        "/api/v1/policies",
        json={
            "name": name,
            "status": "active",
            "content": content,
            "sections": [{"section_ref": "4.2", "text": content, "position": 0}],
        },
    )
    assert created.status_code == 201
    return str(created.json()["id"])


def _ask(client: TestClient, text: str = "How long must customer records be retained?") -> str:
    asked = client.post("/api/v1/questions", json={"question": text, "as_of": "2026-06-01"})
    assert asked.status_code == 202
    assert asked.json()["status"] == "queued"
    return str(asked.json()["id"])


def _run(
    session_factory: sessionmaker[Session],
    provider: object,
    settings: Settings | None = None,
) -> None:
    with session_factory() as session:
        assert process_next_question(
            session,
            settings or Settings(),
            provider,  # type: ignore[arg-type]
            "question-worker",
        )


def _answer(text: str, *citations: tuple[int, str], supported: bool = True) -> ModelAnswer:
    return ModelAnswer(
        supported=supported,
        answer=text,
        citations=[AnswerCitation(source=source, quote=quote) for source, quote in citations],
    )


def test_a_question_is_answered_with_a_checked_citation(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    policy_id = _policy(client)
    with session_factory() as session:
        policy = session.get(Policy, policy_id)
        assert policy is not None
        policy.document_number = "2024-19699"
        policy.citation = "89 FR 71234"
        session.commit()
    question_id = _ask(client)

    _run(session_factory, DeterministicProvider())

    body = client.get(f"/api/v1/questions/{question_id}").json()
    assert body["status"] == "answered"
    assert body["retrieval_mode"] == "keyword"
    assert body["citations"][0]["source"] == 1
    assert body["citations"][0]["section_ref"] == "4.2"
    assert body["citations"][0]["quote"] in RETENTION
    assert body["sources"][0]["source_url"] == "https://www.federalregister.gov/d/2024-19699"
    assert body["sources"][0]["citation"] == "89 FR 71234"
    assert body["validation_errors"] == []
    with session_factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent.event_type).order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    assert events[-3:] == ["question_asked", "question_started", "question_answered"]


def test_no_relevant_source_is_unanswered_without_calling_the_model(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    question_id = _ask(client)
    provider = ScriptedProvider()

    _run(session_factory, provider)

    body = client.get(f"/api/v1/questions/{question_id}").json()
    assert body["status"] == "unanswered"
    assert body["validation_errors"] == ["no_relevant_source"]
    assert body["answer"] is None
    assert provider.requests == []


def test_a_wrong_quote_is_retried_with_feedback_the_model_can_act_on(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _policy(client)
    question_id = _ask(client)
    provider = ScriptedProvider(
        _answer("Records are kept for a long time.", (1, "Records are kept for a very long time.")),
        _answer(
            "Customer records must be kept for seven years.",
            (1, "Customer records must be retained for seven years."),
        ),
    )

    _run(session_factory, provider)

    body = client.get(f"/api/v1/questions/{question_id}").json()
    assert body["status"] == "answered"
    assert provider.requests[0].validation_feedback == ()
    assert provider.requests[1].validation_feedback == (
        "The quote from source 1 is not in its text. Copy the words exactly.",
    )
    assert body["input_tokens"] == 200
    assert body["latency_ms"] == 100


def test_an_answer_that_fails_twice_is_not_shown(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _policy(client)
    question_id = _ask(client)
    invented = _answer("Records are kept for 10 years.", (1, "Records are kept for 10 years."))

    _run(session_factory, ScriptedProvider(invented, invented))

    body = client.get(f"/api/v1/questions/{question_id}").json()
    assert body["status"] == "unanswered"
    assert body["answer"] is None
    assert body["citations"] == []
    assert body["validation_errors"] == [
        "citation_quote_not_found:1",
        "answer_temporal_claim_not_cited:for 10 years",
    ]
    assert body["sources"][0]["section_ref"] == "4.2"


def test_the_model_can_say_the_sources_do_not_cover_it(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _policy(client)
    question_id = _ask(client, "What is the penalty for a late filing?")
    declined = _answer("The sources do not state a penalty.", supported=False)

    _run(session_factory, ScriptedProvider(declined))

    body = client.get(f"/api/v1/questions/{question_id}").json()
    assert body["status"] == "unanswered"
    assert body["validation_errors"] == ["model_reported_insufficient_sources"]


def test_malformed_output_twice_is_unanswered_and_a_provider_error_fails(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _policy(client)
    malformed_id = _ask(client)
    _run(
        session_factory,
        ScriptedProvider(MalformedModelOutputError("bad"), MalformedModelOutputError("bad")),
    )
    failed_id = _ask(client)
    request = httpx.Request("POST", "http://model.test")
    _run(session_factory, ScriptedProvider(httpx.ConnectError("down", request=request)))

    malformed = client.get(f"/api/v1/questions/{malformed_id}").json()
    failed = client.get(f"/api/v1/questions/{failed_id}").json()
    assert malformed["status"] == "unanswered"
    assert malformed["validation_errors"] == ["malformed_model_output"]
    assert failed["status"] == "failed"
    assert failed["error_code"] == "model_answer_failed"


def test_personal_data_is_redacted_before_the_model_sees_the_question(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _policy(client)
    question_id = _ask(client, "Can jane@example.com keep customer records for seven years?")
    provider = ScriptedProvider(
        _answer("Seven years.", (1, "Customer records must be retained for seven years."))
    )

    _run(session_factory, provider)

    assert "jane@example.com" not in provider.requests[0].question
    assert "[e]" in provider.requests[0].question
    body = client.get(f"/api/v1/questions/{question_id}").json()
    assert body["redaction_count"] == 1


def test_long_sections_are_trimmed_before_they_reach_the_model(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    long_text = RETENTION + " " + " ".join(f"Clause {number} applies." for number in range(400))
    _policy(client, long_text)
    _ask(client)
    provider = ScriptedProvider(
        _answer("Seven years.", (1, "Customer records must be retained for seven years."))
    )

    _run(session_factory, provider, Settings(question_source_chars=300))

    shown = provider.requests[0].sources[0].content
    assert len(shown) <= 300
    assert long_text.startswith(shown)


def test_an_unexpected_error_requeues_the_question(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _policy(client)
    question_id = _ask(client)

    _run(session_factory, ScriptedProvider(RuntimeError("crashed")))

    with session_factory() as session:
        question = session.get(PolicyQuestion, question_id)
        assert question is not None
        assert question.status == QuestionStatus.QUEUED
        assert question.attempts == 1


def test_the_reaper_requeues_then_fails_an_expired_question(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    question_id = _ask(client)
    with session_factory() as session:
        for expected in (QuestionStatus.QUEUED, QuestionStatus.FAILED):
            claimed = claim_next_question(session, "gone-worker", 120, "reap-test")
            assert claimed is not None
            claimed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
            reap_expired_questions(session, max_attempts=2, correlation_id="reap-test")
            session.refresh(claimed)
            assert claimed.status == expected
    with session_factory() as session:
        question = session.get(PolicyQuestion, question_id)
        assert question is not None
        assert question.error_code == "question_attempts_exhausted"


def test_only_the_asker_or_an_admin_can_read_a_question(client: TestClient) -> None:
    asked = client.post(
        "/api/v1/questions",
        json={"question": "How long are records kept?"},
        headers={"X-User-ID": "analyst-one", "X-Roles": "analyst"},
    )
    assert asked.status_code == 202
    assert asked.json()["as_of"] == date.today().isoformat()
    question_id = asked.json()["id"]
    other = client.get(
        f"/api/v1/questions/{question_id}",
        headers={"X-User-ID": "analyst-two", "X-Roles": "analyst"},
    )
    assert other.status_code == 404
    assert client.get(f"/api/v1/questions/{question_id}").status_code == 200


def test_validate_answer_requires_quotes_for_dates_and_existing_sources() -> None:
    source = RetrievedPolicy(
        id="p",
        name="reporting",
        version=1,
        section_ref="7",
        heading=None,
        content="Incidents must be reported within 15 days of discovery.",
        position=0,
        score=1.0,
    )
    errors = validate_answer(
        _answer(
            "Report within 15 days, or within 30 days for minor incidents.",
            (1, "Incidents must be reported within 15 days of discovery."),
            (3, "A source that was never supplied to the model."),
        ),
        [source],
    )
    assert errors == [
        "citation_not_retrieved:3",
        "answer_temporal_claim_not_cited:within 30 days",
    ]
    assert validate_answer(_answer("Nothing here.", supported=False), [source]) == []
    assert validate_answer(_answer("Report promptly."), [source]) == ["answer_without_citation"]


def test_ollama_receives_numbered_sources_and_the_answer_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        content = json.dumps(
            {
                "supported": True,
                "answer": "Records are kept for seven years.",
                "citations": [
                    {"source": 1, "quote": "Customer records must be retained for seven years."}
                ],
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": f"```json\n{content}\n```"}}],
                "usage": {"prompt_tokens": 410, "completion_tokens": 38},
            },
        )

    source = RetrievedPolicy(
        id="p",
        name="data-retention",
        version=1,
        section_ref="4.2",
        heading="Retention",
        content=RETENTION,
        position=0,
        score=1.0,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as model_client:
        provider = build_provider(
            Settings(model_provider="ollama", local_model_base_url="http://ollama.test/v1")
        )
        assert isinstance(provider, OpenAICompatibleProvider)
        provider.client = model_client
        response = provider.answer(
            AnswerRequest(
                system_prompt="Answer from the sources.",
                question="How long are customer records kept?",
                as_of=date(2026, 6, 1),
                sources=[source],
            )
        )

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert '"supported"' in messages[0]["content"]
    user = json.loads(messages[1]["content"])
    assert user["untrusted_question"] == "How long are customer records kept?"
    assert user["sources"][0]["source"] == 1
    assert user["sources"][0]["section"] == "4.2"
    assert captured["response_format"] == {"type": "json_object"}
    assert response.answer.citations[0].source == 1
    assert response.input_tokens == 410


@pytest.mark.parametrize("mode", ["vector", "embedding"])
def test_search_reports_every_mode_it_can_run(client: TestClient, mode: str) -> None:
    from app.api.routes.search import get_embedder
    from app.main import app

    _policy(client)

    class _FlatEmbedder:
        def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
            return [(1.0, 0.0) for _ in texts]

    app.dependency_overrides[get_embedder] = lambda: _FlatEmbedder()
    try:
        response = client.get(
            "/api/v1/search",
            params={"q": "customer records", "as_of": "2026-06-01", "mode": mode},
        )
    finally:
        app.dependency_overrides.pop(get_embedder, None)
    assert response.status_code == 200
    assert response.json()["mode"] == mode
