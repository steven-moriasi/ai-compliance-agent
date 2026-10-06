"""Federal Register HTTP client.

The documents.json search API is public. On 2026-10-07 the full-text XML and
raw-text URLs on federalregister.gov returned an HTML access page from this
network, including with a browser user agent. GovInfo publishes the same day's
issue as XML, so that file is the fallback and is cached once per publication
date.
"""

import email.utils
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ingestion.federal_register.parse import (
    extract_document_xml,
    looks_like_html,
    looks_like_xml,
)

_RETRYABLE = {429, 500, 502, 503, 504}
_SEARCH_FIELDS = (
    "document_number",
    "title",
    "type",
    "abstract",
    "publication_date",
    "effective_on",
    "citation",
    "html_url",
    "full_text_xml_url",
    "raw_text_url",
    "cfr_references",
    "docket_ids",
    "correction_of",
    "agencies",
)
_COUNT_CAP = 10000


class FederalRegisterError(Exception):
    """The Federal Register API or its GovInfo fallback could not be read."""


class CfrReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: int | str | None = None
    part: str | None = None
    chapter: str | None = None
    citation_url: str | None = None

    @field_validator("part", "chapter", "citation_url", mode="before")
    @classmethod
    def _text(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return str(value)


class FederalRegisterDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_number: str
    title: str
    type: str = "Rule"
    abstract: str | None = None
    publication_date: date
    effective_on: date | None = None
    citation: str | None = None
    html_url: str | None = None
    full_text_xml_url: str | None = None
    raw_text_url: str | None = None
    cfr_references: list[CfrReference] = Field(default_factory=list)
    docket_ids: list[str] = Field(default_factory=list)
    correction_of: str | None = None

    @field_validator("effective_on", mode="before")
    @classmethod
    def _blank_date(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("correction_of", mode="before")
    @classmethod
    def _correction(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            raw = value.get("document_number")
            return raw if isinstance(raw, str) else None
        return None

    @field_validator("docket_ids", mode="before")
    @classmethod
    def _dockets(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item]


class SearchPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    count: int | None = None
    total_pages: int | None = None
    next_page_url: str | None = None
    results: list[FederalRegisterDocument] = Field(default_factory=list)


def build_user_agent(contact: str) -> str:
    cleaned = " ".join(contact.split()) or "local-development"
    return f"ai-compliance-agent/0.1 ({cleaned})"


def build_search_url(
    *,
    agency: str,
    document_type: str,
    cfr_title: int,
    since: date,
    until: date,
    per_page: int = 1000,
) -> str:
    """Build a documents.json URL. Callers follow next_page_url instead of page numbers."""
    params: list[tuple[str, str]] = [
        ("conditions[agencies][]", agency),
        ("conditions[type][]", document_type),
        ("conditions[cfr][title]", str(cfr_title)),
        ("conditions[publication_date][gte]", since.isoformat()),
        ("conditions[publication_date][lte]", until.isoformat()),
        ("per_page", str(max(1, min(per_page, 1000)))),
        ("order", "oldest"),
    ]
    params.extend(("fields[]", field) for field in _SEARCH_FIELDS)
    return "https://www.federalregister.gov/api/v1/documents.json?" + urlencode(params)


def govinfo_issue_url(publication_date: date) -> str:
    stamp = publication_date.isoformat()
    return f"https://www.govinfo.gov/content/pkg/FR-{stamp}/xml/FR-{stamp}.xml"


class FederalRegisterClient:
    def __init__(
        self,
        *,
        cache_dir: Path,
        contact: str,
        transport: httpx.BaseTransport | None = None,
        min_interval_seconds: float = 0.5,
        timeout_seconds: float = 60.0,
        max_attempts: int = 5,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache_dir = cache_dir
        self._min_interval = min_interval_seconds
        self._max_attempts = max_attempts
        self._sleeper = sleeper
        self._clock = clock
        self._last_request = 0.0
        headers = {
            "User-Agent": build_user_agent(contact),
            "Accept": "application/json, application/xml, text/xml, text/plain, */*",
        }
        if transport is None:
            self._http = httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=True,
                headers=headers,
            )
        else:
            self._http = httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=True,
                headers=headers,
                transport=transport,
            )

    def __enter__(self) -> "FederalRegisterClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def search_documents(
        self,
        *,
        agency: str,
        document_type: str,
        cfr_title: int,
        since: date,
        until: date,
        max_documents: int,
    ) -> list[FederalRegisterDocument]:
        found: list[FederalRegisterDocument] = []
        self._search_window(
            agency=agency,
            document_type=document_type,
            cfr_title=cfr_title,
            since=since,
            until=until,
            max_documents=max_documents,
            found=found,
        )
        return found[:max_documents]

    def fetch_document_body(self, document: FederalRegisterDocument) -> bytes:
        """Return XML for one document, using the disk cache when it is already present."""
        cached = self._document_cache_path(document)
        if cached.is_file():
            return cached.read_bytes()
        for url in (document.full_text_xml_url, document.raw_text_url):
            if not url:
                continue
            body = self._usable_body(self._get_bytes(url), document.document_number)
            if body is not None:
                self._write(cached, body)
                return body
        issue = self._fetch_issue(document.publication_date)
        extracted = extract_document_xml(issue, document.document_number)
        if extracted is None:
            raise FederalRegisterError(
                f"{document.document_number} was not found in the GovInfo issue XML"
            )
        self._write(cached, extracted)
        return extracted

    def _search_window(
        self,
        *,
        agency: str,
        document_type: str,
        cfr_title: int,
        since: date,
        until: date,
        max_documents: int,
        found: list[FederalRegisterDocument],
    ) -> None:
        if len(found) >= max_documents or since > until:
            return
        url = build_search_url(
            agency=agency,
            document_type=document_type,
            cfr_title=cfr_title,
            since=since,
            until=until,
        )
        page = self._read_search_page(url)
        count = page.count or 0
        if count >= _COUNT_CAP and (until - since).days >= 1:
            midpoint = since + timedelta(days=(until - since).days // 2)
            if midpoint <= since:
                midpoint = since
            self._search_window(
                agency=agency,
                document_type=document_type,
                cfr_title=cfr_title,
                since=since,
                until=midpoint,
                max_documents=max_documents,
                found=found,
            )
            self._search_window(
                agency=agency,
                document_type=document_type,
                cfr_title=cfr_title,
                since=midpoint + timedelta(days=1),
                until=until,
                max_documents=max_documents,
                found=found,
            )
            return
        self._drain_search(url, max_documents, found)

    def _drain_search(
        self,
        url: str,
        max_documents: int,
        found: list[FederalRegisterDocument],
    ) -> None:
        seen: set[str] = set()
        next_url: str | None = url
        while next_url and next_url not in seen and len(found) < max_documents:
            seen.add(next_url)
            page = self._read_search_page(next_url)
            for document in page.results:
                if len(found) >= max_documents:
                    return
                found.append(document)
            if not page.next_page_url:
                return
            next_url = urljoin(next_url, page.next_page_url)

    def _read_search_page(self, url: str) -> SearchPage:
        path = self._search_cache_path(url)
        cached = self._read(path)
        if cached is None:
            response = self._request(url)
            self._write(path, response.content)
            payload = response.content
        else:
            payload = cached
        try:
            return SearchPage.model_validate(json.loads(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            raise FederalRegisterError("Federal Register search response was not valid") from exc

    def _fetch_issue(self, publication_date: date) -> bytes:
        path = self.cache_dir / publication_date.isoformat() / "issue.xml"
        cached = self._read(path)
        if cached is not None:
            return cached
        body = self._get_bytes(govinfo_issue_url(publication_date))
        if not looks_like_xml(body):
            raise FederalRegisterError(f"GovInfo issue XML for {publication_date} was not XML")
        self._write(path, body)
        return body

    def _usable_body(self, body: bytes, document_number: str) -> bytes | None:
        if looks_like_html(body):
            return None
        if looks_like_xml(body):
            if b"<FEDREG" in body[:400].upper() or b"<fedreg" in body[:400].lower():
                return extract_document_xml(body, document_number)
            return body
        if body.strip():
            return body
        return None

    def _get_bytes(self, url: str) -> bytes:
        return self._request(url).content

    def _request(self, url: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            self._pace()
            try:
                response = self._http.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 >= self._max_attempts:
                    break
                self._backoff(attempt, None)
                continue
            if response.status_code < 400:
                return response
            if response.status_code in _RETRYABLE and attempt + 1 < self._max_attempts:
                self._backoff(attempt, response)
                continue
            response.raise_for_status()
        raise FederalRegisterError(f"Federal Register request failed for {url}") from last_error

    def _pace(self) -> None:
        if self._min_interval <= 0:
            return
        remaining = self._min_interval - (self._clock() - self._last_request)
        if remaining > 0:
            self._sleeper(remaining)
        self._last_request = self._clock()

    def _backoff(self, attempt: int, response: httpx.Response | None) -> None:
        retry_after = _retry_after_seconds(response)
        if retry_after is None:
            retry_after = min(30.0, 0.5 * (2**attempt))
        self._sleeper(retry_after + (secrets.randbelow(250) / 1000))

    def _document_cache_path(self, document: FederalRegisterDocument) -> Path:
        return (
            self.cache_dir
            / document.publication_date.isoformat()
            / f"{document.document_number}.xml"
        )

    def _search_cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / "searches" / f"{digest}.json"

    def _read(self, path: Path) -> bytes | None:
        if path.is_file():
            return path.read_bytes()
        return None

    def _write(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    if raw.isdigit():
        return float(raw)
    parsed = email.utils.parsedate_to_datetime(raw)
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
