import os
import time
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.domain.enums import CaseStatus, NotificationStatus, PolicyStatus
from app.domain.models import (
    AuditEvent,
    Base,
    ComplianceCase,
    Document,
    NotificationOutbox,
    Policy,
    PolicyQuestion,
    PromptTemplate,
)
from app.services.analysis import AnalysisService
from app.services.cases import claim_next_case, reap_expired_cases
from app.services.notifications import claim_notification, deliver_notification
from app.services.providers import DeterministicProvider, ModelRequest, ModelResponse
from app.services.questions import claim_next_question

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_session_factory() -> Generator[sessionmaker[Session], None, None]:
    database_url = os.getenv("COMPLIANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("COMPLIANCE_TEST_DATABASE_URL is not configured")

    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    test_engine = create_engine(
        database_url,
        execution_options={"schema_translate_map": {None: schema}},
    )
    Base.metadata.create_all(test_engine)
    with test_engine.begin() as connection:
        connection.execute(
            text(
                f'''
                ALTER TABLE "{schema}".policy_sections
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(heading, '') || ' ' || text)
                ) STORED
                '''
            )
        )
    factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def _seed_case(
    factory: sessionmaker[Session],
    *,
    include_policy: bool = False,
) -> str:
    suffix = uuid.uuid4().hex
    document = Document(
        id=str(uuid.uuid4()),
        title="Retention procedure",
        source="postgres-concurrency-test",
        content="Customer records must be retained for seven years.",
        content_hash="a" * 64,
        created_by="test-suite",
    )
    prompt = PromptTemplate(
        id=str(uuid.uuid4()),
        name=f"compliance-review-{suffix}",
        version=1,
        system_prompt="Assess the document only against supplied policy sources.",
        output_schema_version="1",
        active=True,
        created_by="test-suite",
    )
    case = ComplianceCase(
        id=str(uuid.uuid4()),
        document_id=document.id,
        prompt_template_id=prompt.id,
        idempotency_key=f"case-{suffix}",
        requested_by="test-suite",
    )
    with factory() as session:
        session.add_all([document, prompt, case])
        if include_policy:
            session.add(
                Policy(
                    id=str(uuid.uuid4()),
                    name=f"data-retention-{suffix}",
                    version=1,
                    status=PolicyStatus.ACTIVE,
                    content="Customer records must be retained for seven years.",
                    content_hash="b" * 64,
                    created_by="test-suite",
                )
            )
        session.commit()
    return case.id


def _claim_case(
    factory: sessionmaker[Session],
    worker_id: str,
    barrier: Barrier | None = None,
) -> tuple[str, int] | None:
    if barrier is not None:
        barrier.wait()
    with factory() as session:
        claimed = claim_next_case(session, worker_id=worker_id, lease_seconds=120)
        if claimed is None:
            return None
        return claimed.id, claimed.fencing_token


def test_competing_case_claims_skip_locked_and_only_one_wins(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    case_id = _seed_case(postgres_session_factory)

    with postgres_session_factory() as lock_session:
        locked_id = lock_session.scalar(
            select(ComplianceCase.id).where(ComplianceCase.id == case_id).with_for_update()
        )
        assert locked_id == case_id
        with ThreadPoolExecutor(max_workers=1) as executor:
            skipped = executor.submit(
                _claim_case,
                postgres_session_factory,
                "skip-locked-worker",
            ).result(timeout=10)
        assert skipped is None
        lock_session.rollback()

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _claim_case,
                postgres_session_factory,
                worker_id,
                barrier,
            )
            for worker_id in ("worker-one", "worker-two")
        ]
        results = [future.result(timeout=10) for future in futures]

    winners = [result for result in results if result is not None]
    assert winners == [(case_id, 1)]
    with postgres_session_factory() as session:
        persisted = session.get(ComplianceCase, case_id)
        assert persisted is not None
        assert persisted.status == CaseStatus.ANALYZING
        assert persisted.worker_id in {"worker-one", "worker-two"}
        assert persisted.attempts == 1


class _SlowProvider(DeterministicProvider):
    def analyze(self, request: ModelRequest) -> ModelResponse:
        time.sleep(4)
        return super().analyze(request)


def test_slow_provider_renews_the_lease_without_changing_the_token(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    case_id = _seed_case(postgres_session_factory, include_policy=True)
    with postgres_session_factory() as session:
        claimed = claim_next_case(session, worker_id="slow-worker", lease_seconds=2)
        assert claimed is not None
        token = claimed.fencing_token
        started = time.perf_counter()
        result = AnalysisService(
            session,
            Settings(lease_renewal_seconds=1, retrieval_mode="fulltext"),
            _SlowProvider(),
        ).analyze(
            claimed,
            correlation_id="slow-provider",
            worker_id="slow-worker",
        )
        assert time.perf_counter() - started >= 4
        assert result.id == case_id
        assert result.status == CaseStatus.REVIEW_REQUIRED
        assert result.fencing_token == token
        assert result.worker_id == "slow-worker"


def test_stale_fencing_token_cannot_finalize_reclaimed_case(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    case_id = _seed_case(postgres_session_factory, include_policy=True)

    with postgres_session_factory() as stale_session:
        stale_claim = claim_next_case(
            stale_session,
            worker_id="stale-worker",
            lease_seconds=120,
        )
        assert stale_claim is not None
        stale_token = stale_claim.fencing_token
        stale_claim.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        stale_session.commit()

        with postgres_session_factory() as active_session:
            assert reap_expired_cases(
                active_session,
                max_attempts=3,
                correlation_id="postgres-reaper",
            ) == (1, 0)
            active_claim = claim_next_case(
                active_session,
                worker_id="active-worker",
                lease_seconds=120,
            )
            assert active_claim is not None
            assert active_claim.fencing_token == stale_token + 1

        result = AnalysisService(
            stale_session,
            Settings(),
            DeterministicProvider(),
        ).analyze(
            stale_claim,
            correlation_id="stale-analysis",
            worker_id="stale-worker",
        )
        assert result.status == CaseStatus.ANALYZING
        assert result.worker_id == "active-worker"
        assert result.fencing_token == stale_token + 1

    with postgres_session_factory() as session:
        attempt_events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.case_id == case_id,
                    AuditEvent.event_type.in_(["analysis_attempted", "analysis_completed"]),
                )
            )
        )
        assert attempt_events == []


def test_expired_notification_is_reclaimed_with_new_fencing_token(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    case_id = _seed_case(postgres_session_factory)
    notification_id = str(uuid.uuid4())
    with postgres_session_factory() as session:
        session.add(
            NotificationOutbox(
                id=notification_id,
                case_id=case_id,
                event_type="compliance_case_reviewed",
                payload={"case_id": case_id},
                status=NotificationStatus.DELIVERING,
                attempts=1,
                fencing_token=1,
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        session.commit()

    with postgres_session_factory() as stale_session:
        stale_notification = stale_session.get(NotificationOutbox, notification_id)
        assert stale_notification is not None

        with postgres_session_factory() as active_session:
            active_notification = claim_notification(active_session, lease_seconds=60)
            assert active_notification is not None
            assert active_notification.id == notification_id
            assert active_notification.attempts == 2
            assert active_notification.fencing_token == 2

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            stale_result = deliver_notification(
                stale_session,
                stale_notification,
                webhook_url="https://notifications.example.test/reviews",
                webhook_secret=notification_id,
                max_attempts=3,
                client=client,
            )
            assert stale_result.status == NotificationStatus.DELIVERING
            assert stale_result.fencing_token == 2
            assert stale_result.delivered_at is None

            with postgres_session_factory() as active_session:
                active_notification = active_session.get(
                    NotificationOutbox,
                    notification_id,
                )
                assert active_notification is not None
                delivered = deliver_notification(
                    active_session,
                    active_notification,
                    webhook_url="https://notifications.example.test/reviews",
                    webhook_secret=notification_id,
                    max_attempts=3,
                    client=client,
                )
                assert delivered.status == NotificationStatus.SENT
                assert delivered.fencing_token == 2


def test_concurrent_question_claims_take_different_questions(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    with postgres_session_factory() as session:
        session.add_all(
            PolicyQuestion(
                question=f"How long are records kept? ({number})",
                as_of=datetime.now(UTC).date(),
                requested_by="test-suite",
            )
            for number in range(3)
        )
        session.commit()
    barrier = Barrier(4)

    def claim(worker_id: str) -> tuple[str, int] | None:
        barrier.wait()
        with postgres_session_factory() as session:
            claimed = claim_next_question(session, worker_id, 120, f"claim-{worker_id}")
            return None if claimed is None else (claimed.id, claimed.fencing_token)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(claim, [f"question-worker-{index}" for index in range(4)]))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 3
    assert len({question_id for question_id, _ in claimed}) == 3
    assert all(token == 1 for _, token in claimed)
