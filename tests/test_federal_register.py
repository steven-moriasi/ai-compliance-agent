import hashlib
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.models import Policy
from app.ingestion.federal_register.chunking import prepare_sections
from app.ingestion.federal_register.client import (
    FederalRegisterClient,
    build_user_agent,
)
from app.ingestion.federal_register.load import execute_ingest, load_curated
from app.ingestion.federal_register.normalize import (
    has_residual_markup,
    normalize_markup,
    normalize_text,
)
from app.ingestion.federal_register.parse import (
    extract_document_xml,
    parse_federal_register_xml,
    parse_plain_text,
)
from app.ingestion.federal_register.quality import QualityGateError, prepare_corpus
from app.ingestion.federal_register.records import ParsedSection, SourceDocument
from app.services.retrieval import retrieve_policies

FIXTURES = Path(__file__).parent / "fixtures" / "federal_register"
DOCUMENT = {
    "document_number": "2024-19699",
    "title": "Air Plan Approval; New Jersey; NOX SIP Call and Removal of CAIR",
    "type": "Rule",
    "publication_date": "2024-09-03",
    "effective_on": None,
    "citation": "89 FR 71234",
    "html_url": "https://www.federalregister.gov/documents/2024/09/03/2024-19699/example",
    "full_text_xml_url": (
        "https://www.federalregister.gov/documents/full_text/xml/2024/09/03/2024-19699.xml"
    ),
    "raw_text_url": (
        "https://www.federalregister.gov/documents/full_text/text/2024/09/03/2024-19699.txt"
    ),
    "cfr_references": [{"title": 40, "part": "52"}],
    "docket_ids": ["EPA-R02-OAR-2024-0001"],
    "correction_of": None,
}


def _issue_xml() -> bytes:
    raw = (FIXTURES / "2024-19699.xml").read_bytes()
    body = raw.split(b"?>", 1)[1].strip()
    return b'<?xml version="1.0"?><FEDREG>' + body + b"</FEDREG>"


def test_pagination_follows_next_page_url(tmp_path: Path) -> None:
    seen: list[str] = []
    user_agent: list[str] = []
    second = "https://www.federalregister.gov/api/v1/documents.json?cursor=abc"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        user_agent.append(request.headers["user-agent"])
        if "cursor=abc" in str(request.url):
            payload = {
                "count": 2,
                "results": [{**DOCUMENT, "document_number": "2024-20000"}],
                "next_page_url": None,
            }
        else:
            payload = {"count": 2, "results": [DOCUMENT], "next_page_url": second}
        return httpx.Response(200, json=payload)

    with FederalRegisterClient(
        cache_dir=tmp_path,
        contact="local-development",
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
    ) as client:
        documents = client.search_documents(
            agency="environmental-protection-agency",
            document_type="RULE",
            cfr_title=40,
            since=date(2024, 9, 1),
            until=date(2024, 9, 3),
            max_documents=10,
        )

    assert [document.document_number for document in documents] == ["2024-19699", "2024-20000"]
    assert seen[1] == second
    assert user_agent[0] == build_user_agent("local-development")


@pytest.mark.parametrize("status_code", [429, 503])
def test_retries_429_and_503(tmp_path: Path, status_code: int) -> None:
    _assert_retry(tmp_path, status_code)


def test_search_responses_are_cached(tmp_path: Path) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"count": 1, "results": [DOCUMENT], "next_page_url": None})

    kwargs = {
        "cache_dir": tmp_path,
        "contact": "local-development",
        "min_interval_seconds": 0.0,
    }
    with FederalRegisterClient(transport=httpx.MockTransport(handler), **kwargs) as client:
        client.search_documents(
            agency="environmental-protection-agency",
            document_type="RULE",
            cfr_title=40,
            since=date(2024, 9, 3),
            until=date(2024, 9, 3),
            max_documents=5,
        )

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    with FederalRegisterClient(transport=httpx.MockTransport(fail), **kwargs) as client:
        cached = client.search_documents(
            agency="environmental-protection-agency",
            document_type="RULE",
            cfr_title=40,
            since=date(2024, 9, 3),
            until=date(2024, 9, 3),
            max_documents=5,
        )

    assert calls["count"] == 1
    assert cached[0].document_number == "2024-19699"


def test_count_cap_splits_the_date_window(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        start = query["conditions[publication_date][gte]"][0]
        end = query["conditions[publication_date][lte]"][0]
        if start == "2024-01-01" and end == "2024-01-04":
            return httpx.Response(200, json={"count": 10000, "results": [], "next_page_url": None})
        document = {**DOCUMENT, "document_number": start, "publication_date": start}
        return httpx.Response(
            200,
            json={"count": 1, "results": [document], "next_page_url": None},
        )

    with FederalRegisterClient(
        cache_dir=tmp_path,
        contact="local-development",
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
    ) as client:
        documents = client.search_documents(
            agency="environmental-protection-agency",
            document_type="RULE",
            cfr_title=40,
            since=date(2024, 1, 1),
            until=date(2024, 1, 4),
            max_documents=10,
        )

    assert {document.document_number for document in documents} == {"2024-01-01", "2024-01-03"}


def test_html_access_page_falls_back_to_govinfo_issue(tmp_path: Path) -> None:
    issue = _issue_xml()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "full_text" in url:
            page = "<!DOCTYPE html><html><title>Request Access</title></html>"
            return httpx.Response(200, text=page)
        if "govinfo.gov" in url:
            return httpx.Response(200, content=issue)
        return httpx.Response(404)

    with FederalRegisterClient(
        cache_dir=tmp_path,
        contact="local-development",
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
    ) as client:
        from app.ingestion.federal_register.client import FederalRegisterDocument

        body = client.fetch_document_body(FederalRegisterDocument.model_validate(DOCUMENT))

    assert extract_document_xml(issue, "2024-19699") is not None
    assert b"2024-19699" in body
    assert any(tmp_path.rglob("*.xml"))


def test_real_fixtures_parse_sections_and_subscripts() -> None:
    sections = parse_federal_register_xml((FIXTURES / "2024-19699.xml").read_bytes())
    refs = {section.section_ref for section in sections}
    assert "preamble:I" in refs
    assert "§ 52.1570" in refs
    assert any("NOx" in section.text for section in sections)
    for name in ("2025-23424.xml", "2025-23420.xml"):
        parsed = parse_federal_register_xml((FIXTURES / name).read_bytes())
        assert parsed
        assert any(section.section_ref.startswith("§") for section in parsed)


def test_plain_text_fallback_uses_outline_labels() -> None:
    sections = parse_plain_text(
        "I. Background\n\nNitrogen oxides must be monitored at the stack every operating hour.\n"
    )
    assert sections[0].section_ref == "preamble:I"
    assert "Nitrogen oxides" in sections[0].text


def test_chemical_notation_normalizes_without_residual_tags() -> None:
    assert normalize_markup('NO<E T="52">X</E>') == "NOx"
    assert normalize_markup("NO<sub>x</sub>") == "NOx"
    assert normalize_markup("NO<inf>x</inf>") == "NOx"
    assert normalize_markup("PM<sub>2.5</sub>") == "PM2.5"
    assert normalize_markup("SO<sub>2</sub>") == "SO2"
    assert normalize_markup("CO<sub>2</sub>") == "CO2"
    normalized = normalize_markup('NO<E T="52">X</E><em>limit</em>')
    assert normalized == "NOxlimit"
    assert not has_residual_markup(normalized)
    assert normalize_text("regula-\n tions") == "regulations"
    assert normalize_text("non-binding rule") == "non-binding rule"


def test_long_sections_split_on_paragraphs_with_overlap() -> None:
    paragraph = " ".join(f"word{index}" for index in range(100))
    section = ParsedSection("§ 60.5", "Limit", (paragraph, paragraph, paragraph, paragraph))
    chunks, dropped, split = prepare_sections([section])
    assert dropped == 0
    assert split == 1
    assert [chunk.section_ref for chunk in chunks] == ["§ 60.5", "§ 60.5#2"]
    overlap = " ".join(chunks[0].text.split()[-50:])
    assert chunks[1].text.startswith(overlap)


def test_short_sections_are_dropped() -> None:
    chunks, dropped, _split = prepare_sections([ParsedSection("§ 1.1", None, ("too short",))])
    assert dropped == 1
    assert chunks == ()


def test_quality_failure_blocks_load(session_factory: sessionmaker[Session]) -> None:
    paragraph = " ".join(["measured"] * 30)
    section = ParsedSection("document", None, (paragraph,))
    documents = [
        _source("2024-19699", (section,)),
        _source("2024-19699", (section,)),
    ]
    report, curated = prepare_corpus(documents, run_id="quality-block")
    assert report.blocking
    assert "duplicate_document_numbers" in report.reasons
    assert curated == ()
    with session_factory() as session:
        with pytest.raises(QualityGateError):
            load_curated(session, curated, report=report, dataset_version="blocked")
        session.commit()
        assert session.scalars(select(Policy)).all() == []


def test_ingest_is_idempotent_and_versions_content_changes(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    issue = _issue_xml()
    calls = {"govinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "documents.json" in url:
            return httpx.Response(
                200,
                json={"count": 1, "results": [DOCUMENT], "next_page_url": None},
            )
        if "full_text" in url:
            page = "<!DOCTYPE html><html><title>Federal Register :: Request Access</title></html>"
            return httpx.Response(200, text=page)
        if "govinfo.gov" in url:
            calls["govinfo"] += 1
            return httpx.Response(200, content=issue)
        return httpx.Response(404)

    cache = tmp_path / "raw"
    data = tmp_path / "data"
    with FederalRegisterClient(
        cache_dir=cache,
        contact="local-development",
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
    ) as client:
        with session_factory() as session:
            assert execute_ingest(
                session,
                client,
                agency="environmental-protection-agency",
                document_type="RULE",
                cfr_title=40,
                since=date(2024, 9, 3),
                until=date(2024, 9, 3),
                max_documents=5,
                incremental=False,
                data_dir=data,
                git_sha="abc123",
            ) == 0
            assert execute_ingest(
                session,
                client,
                agency="environmental-protection-agency",
                document_type="RULE",
                cfr_title=40,
                since=date(2024, 9, 3),
                until=date(2024, 9, 3),
                max_documents=5,
                incremental=False,
                data_dir=data,
                git_sha="abc123",
            ) == 0
        with session_factory() as session:
            policies = list(session.scalars(select(Policy).order_by(Policy.version)))
            assert len(policies) == 1
            assert policies[0].version == 1
            assert policies[0].status == PolicyStatus.ACTIVE
            assert policies[0].effective_date_source == "publication_date"
            assert policies[0].content_sha256 == policies[0].content_hash
            original = policies[0].content
            _replace_content(session, policies[0], original + " Additional monitoring applies.")
            session.commit()
            versions = list(session.scalars(select(Policy).order_by(Policy.version)))

    assert calls["govinfo"] == 1
    assert [policy.version for policy in versions] == [1, 2]
    assert versions[0].status == PolicyStatus.RETIRED
    assert versions[1].status == PolicyStatus.ACTIVE
    assert list((data / "manifests").glob("*.json"))


def test_proposed_rules_stay_out_of_retrieval(session_factory: sessionmaker[Session]) -> None:
    paragraph = " ".join(["nitrogen"] * 5 + ["oxides"] * 5 + ["monitored"] * 15)
    report, curated = prepare_corpus(
        [_source("2024-30000", (ParsedSection("document", None, (paragraph,)),), "Proposed Rule")],
        run_id="draft-rule",
    )
    assert not report.blocking
    with session_factory() as session:
        load_curated(session, curated, report=report, dataset_version="draft")
        session.commit()
        assert retrieve_policies(session, "nitrogen oxides", date(2026, 6, 1)) == []
        stored = session.scalars(select(Policy)).one()
        assert stored.status == PolicyStatus.DRAFT


def test_cli_uses_the_database_and_cache_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core.config import get_settings
    from app.ingestion.federal_register.__main__ import main

    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            seen["client"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def fake_execute(*_args: object, **kwargs: object) -> int:
        seen["execute"] = kwargs
        return 0

    monkeypatch.setattr(
        "app.ingestion.federal_register.__main__.FederalRegisterClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "app.ingestion.federal_register.__main__.execute_ingest",
        fake_execute,
    )
    database = tmp_path / "cli.db"
    monkeypatch.setenv("COMPLIANCE_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    try:
        code = main(
            [
                "ingest",
                "--since",
                "2024-09-03",
                "--until",
                "2024-09-03",
                "--max-docs",
                "1",
                "--cache-dir",
                str(tmp_path / "cache"),
                "--data-dir",
                str(tmp_path / "data"),
            ]
        )
    finally:
        get_settings.cache_clear()

    assert code == 0
    assert seen["execute"] is not None
    client = seen["client"]
    assert isinstance(client, dict)
    assert client["cache_dir"] == tmp_path / "cache"


def test_cli_rejects_an_inverted_date_window(capsys: pytest.CaptureFixture[str]) -> None:
    from app.ingestion.federal_register.__main__ import main

    with pytest.raises(SystemExit):
        main(["ingest", "--since", "2024-09-04", "--until", "2024-09-03"])
    assert "since" in capsys.readouterr().err.lower()


def test_abbreviations_retrieve_the_other_form(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add(
            Policy(
                id="policy-nox",
                name="nox-limit",
                version=1,
                status=PolicyStatus.ACTIVE,
                content="NOx remains regulated.",
                content_hash=hashlib.sha256(b"NOx remains regulated.").hexdigest(),
                created_by="test",
                sections=[],
            )
        )
        session.commit()
        found = retrieve_policies(session, "Inventory covers nitrogen oxides.", date(2026, 6, 1))
    assert [item.id for item in found] == ["policy-nox"]


def _assert_retry(tmp_path: Path, status_code: int) -> None:
    calls = {"count": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(status_code, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"count": 0, "results": [], "next_page_url": None})

    with FederalRegisterClient(
        cache_dir=tmp_path / str(status_code),
        contact="local-development",
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
        sleeper=delays.append,
    ) as client:
        documents = client.search_documents(
            agency="environmental-protection-agency",
            document_type="RULE",
            cfr_title=40,
            since=date(2024, 9, 3),
            until=date(2024, 9, 3),
            max_documents=5,
        )

    assert documents == []
    assert calls["count"] == 2
    assert delays and delays[0] >= 0


def _source(
    document_number: str,
    sections: tuple[ParsedSection, ...],
    document_type: str = "Rule",
) -> SourceDocument:
    return SourceDocument(
        document_number=document_number,
        title="Sample rule",
        document_type=document_type,
        publication_date=date(2024, 9, 3),
        effective_on=None,
        citation=None,
        source_url=None,
        cfr_references=({"title": 40, "part": "52"},),
        docket_ids=(),
        correction_of=None,
        sections=sections,
    )


def _replace_content(session: Session, policy: Policy, extra_sentence: str) -> None:
    from app.ingestion.federal_register.chunking import SectionChunk
    from app.ingestion.federal_register.quality import CuratedDocument, QualityReport

    section = policy.sections[0]
    updated = SectionChunk(section.section_ref, section.heading, extra_sentence, section.position)
    curated = CuratedDocument(
        document_number=policy.document_number or policy.name,
        title=policy.title or policy.name,
        document_type=policy.document_type or "Rule",
        publication_date=policy.publication_date or date(2024, 9, 3),
        effective_on=None,
        effective_from=policy.effective_from or date(2024, 9, 3),
        effective_date_source=policy.effective_date_source or "publication_date",
        citation=policy.citation,
        source_url=policy.source_url,
        cfr_references=tuple(policy.cfr_references or []),
        docket_ids=tuple(policy.docket_ids or []),
        correction_of=policy.correction_of,
        sections=(updated,),
        content_sha256=hashlib.sha256(extra_sentence.encode()).hexdigest(),
        source_xml_sha256=None,
    )
    report = QualityReport(
        run_id="version-bump",
        documents=1,
        parse_failures=0,
        duplicate_document_numbers=0,
        empty_sections_dropped=0,
        sections_split=0,
        residual_markup=0,
        date_failures=0,
        documents_without_sections=0,
        counts_by_cfr_part={},
        counts_by_document_type={},
        blocking=False,
        reasons=(),
    )
    load_curated(session, (curated,), report=report, dataset_version="version-two")
