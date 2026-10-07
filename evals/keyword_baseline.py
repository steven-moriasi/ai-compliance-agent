"""Keyword baseline that loads eligible sections once.

`retrieve_policies` in keyword mode reads every section on every call. On the
Federal Register corpus that load is tens of seconds, and the rank itself is
the smaller part. This module calls the same scorer after one load. Latency of
the uncached path stays in the retrieval latency report.
"""

import time
from datetime import date

from sqlalchemy.orm import Session

from app.domain.models import Policy, PolicySection
from app.services.retrieval import (
    RetrievedPolicy,
    _eligible_sections,
    _keyword_results,
    _ScoredSection,
    _tokens,
    _WholeDocumentSection,
)

_Prepared = tuple[Policy, PolicySection | _WholeDocumentSection, str, frozenset[str]]


class KeywordBaseline:
    """Cache token sets for one effective date. A second date loads again."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._cache: dict[date, list[_Prepared]] = {}
        self.load_ms = 0.0

    def warmup(self, dates: set[date]) -> None:
        """Load each effective-date window once, before per-query timing starts."""
        for case_date in dates:
            started = time.perf_counter()
            self._prepared(case_date)
            self.load_ms += (time.perf_counter() - started) * 1000

    def search(
        self,
        query: str,
        case_date: date,
        limit: int,
        predictions: dict[str, float],
    ) -> list[RetrievedPolicy]:
        document_tokens = _tokens(query)
        scored = [
            _ScoredSection(
                policy=policy,
                section=section,
                lexical_score=len(document_tokens & tokens) / max(len(document_tokens), 1),
                embedding_score=0.0,
                body=body,
            )
            for policy, section, body, tokens in self._prepared(case_date)
        ]
        return _keyword_results(scored, limit, predictions)

    def _prepared(self, case_date: date) -> list[_Prepared]:
        cached = self._cache.get(case_date)
        if cached is not None:
            return cached
        prepared = [
            (policy, section, body, frozenset(_tokens(body)))
            for policy, section, body in _eligible_sections(self._session, case_date)
        ]
        self._cache[case_date] = prepared
        return prepared
