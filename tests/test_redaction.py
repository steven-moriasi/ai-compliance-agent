import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domain.enums import CaseStatus
from app.domain.models import AuditEvent, Document
from app.services.analysis import AnalysisService
from app.services.cases import claim_next_case
from app.services.providers import DeterministicProvider, ModelRequest, ModelResponse
from app.services.redaction import install_log_redaction, redact_personal_data
from app.services.retrieval import TOKEN_PATTERN
from tests.test_workflow import create_catalog


def test_personal_data_becomes_short_placeholders() -> None:
    source = (
        "nox limit for pat@example.com, 415-555-0130, 900-12-3456, "
        "and 4111111111111111 on 2024-10-11."
    )
    redacted = redact_personal_data(source)

    assert redacted.count == 4
    assert redacted.text == "nox limit for [e], [p], [s], and [c] on 2024-10-11."
    assert set(TOKEN_PATTERN.findall(redacted.text.lower())) == {
        "nox",
        "limit",
        "for",
        "and",
        "2024",
    }
    assert not TOKEN_PATTERN.findall("[e][p][s][c]")


def test_unassigned_digit_runs_stay_in_place() -> None:
    source = "Batch 1234567890123456 is not a card. See 40 CFR 60.420."
    redacted = redact_personal_data(source)

    assert redacted.count == 0
    assert redacted.text == source


def test_log_records_and_tracebacks_drop_personal_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_log_redaction()
    logger = logging.getLogger("redaction-test")
    with caplog.at_level(logging.WARNING, logger="redaction-test"):
        logger.warning("reach pat@example.com about 415-555-0130")
        try:
            raise ValueError("stored 900-12-3456")
        except ValueError:
            logger.exception("provider failed")

    assert "pat@example.com" not in caplog.text
    assert "415-555-0130" not in caplog.text
    assert "[e]" in caplog.text
    assert "[p]" in caplog.text
    formatted = logging.Formatter().format(caplog.records[-1])
    assert "900-12-3456" not in formatted
    assert "[s]" in formatted


def test_analysis_redacts_the_model_copy_and_keeps_the_source(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    install_log_redaction()
    content = (
        "Customer records must be retained for seven years. "
        "Contact pat@example.com or 415-555-0130. "
        "Reference 900-12-3456 and 4111111111111111."
    )
    _, prompt, document = create_catalog(client, content)
    created = client.post(
        "/api/v1/cases",
        json={"document_id": document["id"], "prompt_template_id": prompt["id"]},
        headers={"X-Idempotency-Key": "redaction-case-0001"},
    )
    assert created.status_code == 202
    assert created.json()["redaction_count"] is None

    provider = _RecordingProvider()
    with session_factory() as session:
        case = claim_next_case(session, worker_id="redaction-worker", lease_seconds=120)
        assert case is not None
        analyzed = AnalysisService(session, Settings(), provider).analyze(
            case,
            correlation_id="redaction-correlation",
            worker_id="redaction-worker",
        )
        assert analyzed.status == CaseStatus.REVIEW_REQUIRED
        stored = session.get(Document, document["id"])
        assert stored is not None
        assert "pat@example.com" in stored.content
        completed = session.scalar(
            select(AuditEvent).where(
                AuditEvent.case_id == analyzed.id,
                AuditEvent.event_type == "analysis_completed",
            )
        )
        assert completed is not None
        assert completed.details["redaction_count"] == 4
        assert "pat@example.com" not in json.dumps(completed.details)

    assert provider.requests
    sent = provider.requests[0].document
    assert "pat@example.com" not in sent
    assert "415-555-0130" not in sent
    assert "900-12-3456" not in sent
    assert "4111111111111111" not in sent
    assert "[e]" in sent and "[p]" in sent and "[s]" in sent and "[c]" in sent
    assert provider.requests[0].policies

    result = client.get(f"/api/v1/cases/{created.json()['id']}")
    assert result.status_code == 200
    assert result.json()["redaction_count"] == 4


class _RecordingProvider:
    name = "recording"
    model = "recording-1"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self._inner = DeterministicProvider()

    def analyze(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._inner.analyze(request)
