import os
import uuid
from collections.abc import Generator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import Settings
from app.domain.enums import CaseStatus, PolicyStatus
from app.domain.models import AuditEvent, Base, Policy, PolicySection
from app.services.analysis import AnalysisService
from app.services.cases import claim_next_case
from app.services.embeddings import RetrievalUnavailable
from app.services.providers import DeterministicProvider
from app.services.retrieval import RetrievedPolicy, retrieve_policies
from app.services.vectors import fuse_ranks, vector_literal
from tests.test_workflow import create_catalog


def test_fuse_ranks_rewards_a_section_found_by_both_lists() -> None:
    agreed = _hit("agreed", "1")
    lexical_only = _hit("lexical", "2")
    fused = fuse_ranks([agreed, lexical_only], [agreed], 2)
    assert [item.section_ref for item in fused] == ["1", "2"]
    assert fused[0].lexical_score == agreed.lexical_score
    assert fused[0].embedding_score == agreed.embedding_score


def test_vector_literal_rejects_the_wrong_width() -> None:
    with pytest.raises(RetrievalUnavailable, match="384"):
        vector_literal([0.0, 1.0])


def test_hybrid_without_a_model_falls_back_to_keyword(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    def _missing(model_name: str, revision: str | None = None) -> None:
        raise RetrievalUnavailable("retrieval extra is not installed")

    monkeypatch.setattr("app.services.analysis.sentence_transformer_embedder", _missing)
    _, prompt, document = create_catalog(
        client,
        "Customer records must be retained for seven years.",
    )
    created = client.post(
        "/api/v1/cases",
        json={"document_id": document["id"], "prompt_template_id": prompt["id"]},
        headers={"X-Idempotency-Key": "vector-fallback-0001"},
    )
    assert created.status_code == 202
    with session_factory() as session:
        case = claim_next_case(session, worker_id="fallback-worker", lease_seconds=120)
        assert case is not None
        analyzed = AnalysisService(
            session,
            Settings(retrieval_mode="hybrid"),
            DeterministicProvider(),
        ).analyze(case, correlation_id="fallback", worker_id="fallback-worker")
        found = session.scalar(
            select(AuditEvent).where(
                AuditEvent.case_id == analyzed.id,
                AuditEvent.event_type == "retrieval_fallback",
            )
        )
    assert analyzed.status == CaseStatus.REVIEW_REQUIRED
    assert found is not None
    assert found.details["from_mode"] == "hybrid"


def _hit(name: str, section_ref: str) -> RetrievedPolicy:
    return RetrievedPolicy(
        id=name,
        name=name,
        version=1,
        section_ref=section_ref,
        heading=None,
        content="text",
        position=0,
        score=1.0,
        lexical_score=0.4,
        embedding_score=0.8,
    )


@pytest.fixture
def postgres_session_factory() -> Generator[sessionmaker[Session], None, None]:
    database_url = os.getenv("COMPLIANCE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("COMPLIANCE_TEST_DATABASE_URL is not configured")
    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    test_engine = create_engine(
        database_url,
        execution_options={"schema_translate_map": {None: schema}},
    )
    Base.metadata.create_all(test_engine)
    with test_engine.begin() as connection:
        connection.execute(
            text(
                f'''
                CREATE TABLE "{schema}".section_embeddings (
                    section_id VARCHAR(36) NOT NULL,
                    model_id VARCHAR(200) NOT NULL,
                    content_sha256 CHAR(64) NOT NULL,
                    embedding vector(384) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (section_id, model_id)
                )
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


def test_vector_hits_convert_cosine_distance(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.vectors import _vector_hits

    class _Embed:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _text in texts]

    with session_factory() as session:
        monkeypatch.setattr(
            session,
            "execute",
            lambda _statement, _params: _Rows([_vector_row(0.25)]),
        )
        hits = _vector_hits(session, "stack", date(2026, 6, 1), 5, _Embed(), "model@rev")
    assert hits[0].embedding_score == pytest.approx(0.75)
    assert hits[0].id == "policy-vector"


def test_vector_search_reports_a_missing_index(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import ProgrammingError

    from app.services.vectors import _vector_hits

    class _Embed:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _text in texts]

    def _execute(_statement: object, _params: object) -> object:
        raise ProgrammingError("select", {}, Exception("missing relation"))

    with session_factory() as session:
        monkeypatch.setattr(session, "execute", _execute)
        with pytest.raises(RetrievalUnavailable, match="not available"):
            _vector_hits(session, "stack", date(2026, 6, 1), 5, _Embed(), "model@rev")


def test_vector_search_reports_an_unbuilt_model(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.vectors import _vector_hits

    class _Embed:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _text in texts]

    def _execute(_statement: object, _params: object) -> _Rows | _Empty:
        if "count" in str(_statement) or "SELECT 1" in str(_statement):
            return _Empty()
        return _Rows([])

    with session_factory() as session:
        monkeypatch.setattr(session, "execute", _execute)
        with pytest.raises(RetrievalUnavailable, match="not built"):
            _vector_hits(session, "stack", date(2026, 6, 1), 5, _Embed(), "model@rev")


def test_vector_search_needs_an_embedder(session_factory: sessionmaker[Session]) -> None:
    from app.services.vectors import _vector_hits

    with session_factory() as session:
        with pytest.raises(RetrievalUnavailable, match="embedder"):
            _vector_hits(session, "stack", date(2026, 6, 1), 5, None, "model@rev")


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


class _Empty:
    def first(self) -> None:
        return None


def _vector_row(distance: float) -> dict[str, object]:
    return {
        "policy_id": "policy-vector",
        "name": "vector-rule",
        "version": 1,
        "section_ref": "1",
        "heading": None,
        "content": "Stack limit.",
        "position": 0,
        "distance": distance,
    }


@pytest.mark.postgres
def test_vector_search_orders_by_stored_distance(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    stored = [1.0] + [0.0] * 383
    with postgres_session_factory() as session:
        policy = Policy(
            id="policy-vector",
            name="vector-rule",
            version=1,
            status=PolicyStatus.ACTIVE,
            content="Stack limit.",
            content_hash="c" * 64,
            created_by="test",
            sections=[
                PolicySection(
                    id="section-vector",
                    section_ref="1",
                    heading=None,
                    text="Stack limit.",
                    position=0,
                )
            ],
        )
        session.add(policy)
        session.commit()
        schema = _schema(session)
        insert_sql = """
            INSERT INTO "{schema}".section_embeddings
                (section_id, model_id, content_sha256, embedding)
            VALUES
                ('section-vector', 'test@rev', '{digest}', CAST(:embedding AS vector))
            """.format(schema=schema, digest="d" * 64)
        session.execute(text(insert_sql), {"embedding": vector_literal(stored)})
        session.commit()

        class _Fixed:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [stored for _text in texts]

        found = retrieve_policies(
            session,
            "stack",
            date(2026, 6, 1),
            mode="vector",
            embedder=_Fixed(),
            embedding_model_id="test@rev",
        )
    assert [item.id for item in found] == ["policy-vector"]
    assert found[0].embedding_score == pytest.approx(1.0)


def _schema(session: Session) -> str:
    bind = session.get_bind()
    assert bind is not None
    schema = (bind.get_execution_options().get("schema_translate_map") or {}).get(None)
    assert isinstance(schema, str)
    return schema
