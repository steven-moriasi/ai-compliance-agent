import os
import uuid
from collections.abc import Generator
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.domain.enums import PolicyStatus
from app.domain.models import Base, Policy, PolicySection
from app.services.embeddings import RetrievalUnavailable
from app.services.fulltext import websearch_query
from app.services.retrieval import resolve_retrieval_mode, retrieve_policies

pytestmark = pytest.mark.postgres


def test_websearch_query_adds_synonym_or_terms() -> None:
    assert websearch_query("nitrogen oxides from the stack") == (
        "nitrogen oxides from the stack OR nox"
    )
    assert websearch_query("NOx") == 'nox OR "nitrogen oxides"'
    assert websearch_query("pm2.5 at the stack") == (
        'pm2.5 at the stack OR "fine particulate matter"'
    )
    assert websearch_query("stack inventory") == "stack inventory"


def test_sqlite_refuses_fulltext_and_postgres_defaults_to_it(
    session_factory: sessionmaker[Session],
) -> None:
    assert resolve_retrieval_mode(None, "postgresql") == "fulltext"
    assert resolve_retrieval_mode(None, "sqlite") == "keyword"
    assert resolve_retrieval_mode("keyword", "postgresql") == "keyword"
    with session_factory() as session:
        with pytest.raises(RetrievalUnavailable, match="PostgreSQL"):
            retrieve_policies(session, "nitrogen oxides", date(2026, 6, 1), mode="fulltext")


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


def test_fulltext_respects_status_dates_and_synonyms(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    with postgres_session_factory() as session:
        session.add_all(
            (
                _policy(
                    "policy-nox",
                    "standards",
                    "The nox limit applies at the stack.",
                    status=PolicyStatus.ACTIVE,
                ),
                _policy(
                    "policy-draft",
                    "draft-standards",
                    "The nox limit applies at the stack.",
                    status=PolicyStatus.DRAFT,
                ),
                _policy(
                    "policy-expired",
                    "old-standards",
                    "The nox limit applies at the stack.",
                    status=PolicyStatus.ACTIVE,
                    effective_to=date(2020, 1, 1),
                ),
                _policy(
                    "policy-section",
                    "part-sixty",
                    "See the numbered requirement.",
                    sections=[
                        PolicySection(
                            section_ref="60.4",
                            heading="Address",
                            text="Reports go to the administrator under 60.4.",
                            position=0,
                        )
                    ],
                ),
            )
        )
        session.commit()
        oxides = retrieve_policies(
            session,
            "nitrogen oxides from the stack",
            date(2026, 6, 1),
            mode="fulltext",
        )
        numbered = retrieve_policies(session, "60.4", date(2026, 6, 1), mode="fulltext")

    assert [item.id for item in oxides] == ["policy-nox"]
    assert [item.id for item in numbered] == ["policy-section"]
    assert numbered[0].section_ref == "60.4"


def _policy(
    policy_id: str,
    name: str,
    content: str,
    *,
    status: PolicyStatus = PolicyStatus.ACTIVE,
    effective_to: date | None = None,
    sections: list[PolicySection] | None = None,
) -> Policy:
    return Policy(
        id=policy_id,
        name=name,
        version=1,
        status=status,
        content=content,
        content_hash="b" * 64,
        effective_to=effective_to,
        created_by="test",
        sections=sections
        or [
            PolicySection(section_ref="1", heading=None, text=content, position=0),
        ],
    )
