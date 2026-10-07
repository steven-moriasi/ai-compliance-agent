import importlib.machinery
import sys
import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domain.enums import CaseStatus
from app.domain.models import AuditEvent, ComplianceCase
from app.services import embeddings
from app.services.cases import claim_next_case, release_failed_claim
from app.services.embeddings import RetrievalUnavailable, sentence_transformer_embedder
from app.services.providers import DeterministicProvider, ModelRequest, ModelResponse
from app.worker import load_worker_embedder, process_next_case
from tests.test_workflow import create_catalog


class _FailingProvider:
    name = "failing"
    model = "failing-1"

    def analyze(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("unexpected failure quoting " + request.document)


def _queue_case(client: TestClient, key: str) -> str:
    _, prompt, document = create_catalog(
        client,
        "Customer records must be retained for seven years.",
    )
    created = client.post(
        "/api/v1/cases",
        json={"document_id": document["id"], "prompt_template_id": prompt["id"]},
        headers={"X-Idempotency-Key": key},
    )
    assert created.status_code == 202
    return str(created.json()["id"])


def _events(session: Session, case_id: str) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.created_at)
        )
    )


def test_embedding_model_loads_once_per_name_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[tuple[str, str | None]] = []

    def _load(model_name: str, revision: str | None) -> object:
        loads.append((model_name, revision))
        return object()

    monkeypatch.setattr(embeddings, "_LOADED_EMBEDDERS", {})
    monkeypatch.setattr(embeddings, "_load_sentence_transformer", _load)

    first = sentence_transformer_embedder("model", "rev-a")
    second = sentence_transformer_embedder("model", "rev-a")
    sentence_transformer_embedder("model", "rev-b")

    assert first is second
    assert loads == [("model", "rev-a"), ("model", "rev-b")]


def test_model_load_errors_fall_back_instead_of_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    class _Unloadable:
        def __init__(self, model_name: str, **_kwargs: object) -> None:
            attempts.append(model_name)
            raise OSError("hub unreachable and no cached snapshot")

    fake = types.ModuleType("sentence_transformers")
    fake.__spec__ = importlib.machinery.ModuleSpec("sentence_transformers", None)
    fake.SentenceTransformer = _Unloadable  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setattr(embeddings, "_LOADED_EMBEDDERS", {})

    for _ in range(2):
        with pytest.raises(RetrievalUnavailable, match=r"could not be loaded \(OSError\)"):
            sentence_transformer_embedder("model", "rev-a")
    assert attempts == ["model", "model"]


def test_worker_skips_the_model_when_retrieval_is_lexical() -> None:
    assert load_worker_embedder(Settings(retrieval_mode="fulltext"), "postgresql") is None
    assert load_worker_embedder(Settings(), "sqlite") is None


def test_worker_loads_the_model_once_and_remembers_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _missing(model_name: str, revision: str | None = None) -> None:
        calls.append(model_name)
        raise RetrievalUnavailable("retrieval extra is not installed")

    monkeypatch.setattr("app.worker.sentence_transformer_embedder", _missing)
    loader = load_worker_embedder(Settings(), "postgresql")

    assert loader is not None
    for _ in range(3):
        with pytest.raises(RetrievalUnavailable, match="retrieval extra"):
            loader()
    assert len(calls) == 1


def test_claim_is_audited_before_analysis_finishes(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    case_id = _queue_case(client, "worker-claim-0001")
    with session_factory() as session:
        claimed = claim_next_case(session, worker_id="audit-worker", lease_seconds=120)
        assert claimed is not None
        events = _events(session, case_id)

    assert [event.event_type for event in events] == ["case_requested", "analysis_started"]
    assert events[1].actor_id == "audit-worker"
    assert events[1].details == {"attempt": 1, "fencing_token": 1}


def test_unexpected_error_requeues_the_case_without_the_message(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    case_id = _queue_case(client, "worker-error-0001")
    with session_factory() as session:
        claimed = process_next_case(session, Settings(), _FailingProvider(), "error-worker")
        assert claimed is True
        case = session.get(ComplianceCase, case_id, populate_existing=True)
        assert case is not None
        assert case.status == CaseStatus.QUEUED
        assert case.worker_id is None
        requeued = _events(session, case_id)[-1]

    assert requeued.event_type == "analysis_requeued"
    assert requeued.details["error"] == "RuntimeError"
    assert "seven years" not in str(requeued.details)


def test_unexpected_error_on_the_last_attempt_fails_the_case(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    case_id = _queue_case(client, "worker-error-0002")
    settings = Settings(analysis_max_attempts=2)
    with session_factory() as session:
        for _ in range(2):
            assert process_next_case(session, settings, _FailingProvider(), "error-worker")
        case = session.get(ComplianceCase, case_id, populate_existing=True)
        assert case is not None
        assert case.status == CaseStatus.FAILED
        assert case.error_code == "analysis_worker_error"
        assert case.attempts == 2
        assert _events(session, case_id)[-1].event_type == "analysis_abandoned"
        assert process_next_case(session, settings, _FailingProvider(), "error-worker") is False


def test_release_does_nothing_for_a_stale_fencing_token(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    case_id = _queue_case(client, "worker-stale-0001")
    with session_factory() as session:
        claimed = claim_next_case(session, worker_id="current-worker", lease_seconds=120)
        assert claimed is not None
        released = release_failed_claim(
            session,
            case_id=case_id,
            worker_id="current-worker",
            fencing_token=claimed.fencing_token - 1,
            max_attempts=3,
            correlation_id="stale-release",
            error_type="RuntimeError",
        )
        case = session.get(ComplianceCase, case_id, populate_existing=True)

    assert released is None
    assert case is not None
    assert case.status == CaseStatus.ANALYZING


def test_worker_completes_a_case_with_the_reference_provider(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    case_id = _queue_case(client, "worker-success-0001")
    with session_factory() as session:
        assert process_next_case(session, Settings(), DeterministicProvider(), "ok-worker")
        case = session.get(ComplianceCase, case_id, populate_existing=True)

    assert case is not None
    assert case.status == CaseStatus.REVIEW_REQUIRED
