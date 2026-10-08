import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import CaseStatus
from app.domain.models import Base, ComplianceCase, Document, PromptTemplate
from app.services.cases import claim_next_case, renew_case_lease
from app.services.lease import lease_heartbeat


def test_renewal_keeps_the_token_and_refuses_an_expired_lease(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        case_id = _seed(session)
        claimed = claim_next_case(session, worker_id="lease-worker", lease_seconds=30)
        assert claimed is not None
        token = claimed.fencing_token
        assert renew_case_lease(session, case_id, "lease-worker", token, 30)
        session.expire_all()
        refreshed = session.get(ComplianceCase, case_id)
        assert refreshed is not None
        assert refreshed.fencing_token == token
        assert refreshed.lease_expires_at is not None
        assert _aware(refreshed.lease_expires_at) > datetime.now(UTC) + timedelta(seconds=20)
        refreshed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        assert not renew_case_lease(session, case_id, "lease-worker", token, 30)
        assert not renew_case_lease(session, case_id, "lease-worker", token + 1, 30)


def test_heartbeat_extends_a_lease_that_would_otherwise_expire(tmp_path: Path) -> None:
    root = tmp_path
    engine = create_engine(
        f"sqlite:///{root / 'lease.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        case_id = _seed(session)
        claimed = claim_next_case(session, worker_id="heartbeat-worker", lease_seconds=1)
        assert claimed is not None
        token = claimed.fencing_token
    with lease_heartbeat(
        engine,
        record_id=case_id,
        worker_id="heartbeat-worker",
        fencing_token=token,
        lease_seconds=30,
        interval_seconds=0.2,
    ):
        time.sleep(0.8)
    with factory() as session:
        refreshed = session.get(ComplianceCase, case_id)
        assert refreshed is not None
        assert refreshed.fencing_token == token
        assert refreshed.status == CaseStatus.ANALYZING
        assert refreshed.lease_expires_at is not None
        assert _aware(refreshed.lease_expires_at) > datetime.now(UTC) + timedelta(seconds=10)
    engine.dispose()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _seed(session: Session) -> str:
    suffix = uuid.uuid4().hex
    document = Document(
        id=str(uuid.uuid4()),
        title="Retention procedure",
        source="lease-test",
        content="Customer records must be retained for seven years.",
        content_hash="a" * 64,
        created_by="test-suite",
    )
    prompt = PromptTemplate(
        id=str(uuid.uuid4()),
        name=f"review-{suffix}",
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
        idempotency_key=f"lease-{suffix}",
        requested_by="test-suite",
    )
    session.add_all([document, prompt, case])
    session.commit()
    return case.id
