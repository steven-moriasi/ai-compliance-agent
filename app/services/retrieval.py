import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import PolicyStatus
from app.domain.models import Policy, PolicySection
from app.ingestion.federal_register.synonyms import expand_tokens
from app.services.classifier import CfrPrior, cfr_labels, predict_cfr_parts
from app.services.embeddings import (
    Embedder,
    RetrievalUnavailable,
    cosine,
    section_embedding_key,
    section_embedding_text,
)

TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")
RRF_K = 60
PRIOR_WEIGHT = 0.05
RetrievalMode = Literal["keyword", "fulltext", "vector", "embedding", "hybrid"]


@dataclass(frozen=True)
class _WholeDocumentSection:
    section_ref: str
    heading: str | None
    text: str
    position: int


@dataclass
class _ScoredSection:
    policy: Policy
    section: PolicySection | _WholeDocumentSection
    lexical_score: float
    embedding_score: float
    body: str


@dataclass(frozen=True)
class RetrievedPolicy:
    id: str
    name: str
    version: int
    section_ref: str
    heading: str | None
    content: str
    position: int
    score: float
    lexical_score: float = 0.0
    embedding_score: float = 0.0
    section_id: str = ""
    document_number: str | None = None


def _tokens(value: str) -> set[str]:
    """Token overlap stays lexical. Listed abbreviations add tokens only when a form is present."""
    return expand_tokens(value, set(TOKEN_PATTERN.findall(value.lower())))


def resolve_retrieval_mode(requested: RetrievalMode | None, dialect: str) -> RetrievalMode:
    """PostgreSQL defaults to hybrid. SQLite and explicit keyword stay on the Python scan.

    The default is the mode that won the curated set in
    `evals/reports/retrieval_eval_2026-10-07.json`. Full text remains available by name.
    """
    if requested is not None:
        return requested
    if dialect == "postgresql":
        return "hybrid"
    return "keyword"


def retrieve_policies(
    session: Session,
    document: str,
    case_date: date,
    limit: int = 5,
    *,
    mode: RetrievalMode | None = None,
    embedder: Embedder | None = None,
    embedding_index: Mapping[str, Sequence[float]] | None = None,
    embedding_model_id: str | None = None,
    cfr_prior: CfrPrior | None = None,
) -> list[RetrievedPolicy]:
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    selected = resolve_retrieval_mode(mode, dialect)
    if selected in {"vector", "embedding", "hybrid"} and dialect == "postgresql":
        from app.services.vectors import retrieve_semantic

        predictions = predict_cfr_parts(cfr_prior, document) if cfr_prior is not None else {}
        return retrieve_semantic(
            session,
            document,
            case_date,
            limit,
            predictions,
            mode=selected,
            embedder=embedder,
            model_id=embedding_model_id or "",
        )
    if selected == "fulltext":
        if dialect != "postgresql":
            raise RetrievalUnavailable("full-text retrieval requires PostgreSQL")
        from app.services.fulltext import retrieve_fulltext

        predictions = predict_cfr_parts(cfr_prior, document) if cfr_prior is not None else {}
        return retrieve_fulltext(session, document, case_date, limit, predictions)
    document_tokens = _tokens(document)
    predictions = predict_cfr_parts(cfr_prior, document) if cfr_prior is not None else {}
    candidates = _eligible_sections(session, case_date)
    scored = [
        _ScoredSection(
            policy=policy,
            section=section,
            lexical_score=len(document_tokens & _tokens(body)) / max(len(document_tokens), 1),
            embedding_score=0.0,
            body=body,
        )
        for policy, section, body in candidates
    ]
    if selected == "keyword":
        return _keyword_results(scored, limit, predictions)
    if embedder is None and not embedding_index:
        raise RetrievalUnavailable("embedding or hybrid search needs an embedder or a built index")
    _apply_embedding_scores(document, scored, embedder, embedding_index)
    if selected == "embedding":
        return _embedding_results(scored, limit, predictions)
    return _hybrid_results(scored, limit, predictions)


def _eligible_sections(
    session: Session,
    case_date: date,
) -> list[tuple[Policy, PolicySection | _WholeDocumentSection, str]]:
    policies = session.scalars(
        select(Policy)
        .options(selectinload(Policy.sections))
        .where(
            Policy.status == PolicyStatus.ACTIVE,
            or_(Policy.effective_from.is_(None), Policy.effective_from <= case_date),
            or_(Policy.effective_to.is_(None), Policy.effective_to > case_date),
        )
        .order_by(Policy.name, Policy.version)
    )
    eligible: list[tuple[Policy, PolicySection | _WholeDocumentSection, str]] = []
    for policy in policies:
        sections: list[PolicySection | _WholeDocumentSection]
        if policy.sections:
            sections = [*policy.sections]
        else:
            sections = [
                _WholeDocumentSection(
                    section_ref="document",
                    heading=policy.name,
                    text=policy.content,
                    position=0,
                )
            ]
        for section in sections:
            eligible.append(
                (policy, section, section_embedding_text(section.heading, section.text))
            )
    return eligible


def _apply_embedding_scores(
    document: str,
    scored: list[_ScoredSection],
    embedder: Embedder | None,
    embedding_index: Mapping[str, Sequence[float]] | None,
) -> None:
    query_vector = _query_vector(document, embedder, embedding_index)
    missing: list[_ScoredSection] = []
    for item in scored:
        vector = None if embedding_index is None else embedding_index.get(
            section_embedding_key(item.section.heading, item.section.text)
        )
        if vector is None:
            missing.append(item)
            continue
        item.embedding_score = cosine(query_vector, vector)
    if not missing or embedder is None:
        return
    vectors = embedder.embed([item.body for item in missing])
    if len(vectors) != len(missing):
        raise RetrievalUnavailable("embedder returned a different number of vectors than texts")
    for item, vector in zip(missing, vectors, strict=True):
        item.embedding_score = cosine(query_vector, vector)


def _query_vector(
    document: str,
    embedder: Embedder | None,
    embedding_index: Mapping[str, Sequence[float]] | None,
) -> Sequence[float]:
    if embedder is not None:
        vectors = embedder.embed([document])
        if len(vectors) != 1:
            raise RetrievalUnavailable("embedder did not return a query vector")
        return vectors[0]
    if embedding_index:
        raise RetrievalUnavailable("an embedding index still needs an embedder for the query")
    raise RetrievalUnavailable("embedding or hybrid search needs an embedder or a built index")


def _keyword_results(
    scored: list[_ScoredSection],
    limit: int,
    predictions: Mapping[str, float],
) -> list[RetrievedPolicy]:
    ranked = [item for item in scored if item.lexical_score > 0]
    ranked.sort(key=lambda item: _adjusted_order(item, item.lexical_score, predictions))
    return [
        _result(item, item.lexical_score + _prior_adjustment(item, predictions))
        for item in ranked[:limit]
    ]


def _embedding_results(
    scored: list[_ScoredSection],
    limit: int,
    predictions: Mapping[str, float],
) -> list[RetrievedPolicy]:
    ranked = [item for item in scored if item.embedding_score > 0]
    ranked.sort(key=lambda item: _adjusted_order(item, item.embedding_score, predictions))
    return [
        _result(item, item.embedding_score + _prior_adjustment(item, predictions))
        for item in ranked[:limit]
    ]


def _hybrid_results(
    scored: list[_ScoredSection],
    limit: int,
    predictions: Mapping[str, float],
) -> list[RetrievedPolicy]:
    lexical_ranks = {
        id(item): rank
        for rank, item in enumerate(
            sorted((item for item in scored if item.lexical_score > 0), key=_candidate_order),
            start=1,
        )
    }
    embedding_ranks = {
        id(item): rank
        for rank, item in enumerate(
            sorted(
                (item for item in scored if item.embedding_score > 0),
                key=lambda item: (-item.embedding_score, *_candidate_order(item)[1:]),
            ),
            start=1,
        )
    }
    fused: list[tuple[float, _ScoredSection]] = []
    for item in scored:
        score = 0.0
        lexical_rank = lexical_ranks.get(id(item))
        embedding_rank = embedding_ranks.get(id(item))
        if lexical_rank is not None:
            score += 1 / (RRF_K + lexical_rank)
        if embedding_rank is not None:
            score += 1 / (RRF_K + embedding_rank)
        if score > 0:
            fused.append((score + _prior_adjustment(item, predictions), item))
    fused.sort(key=lambda pair: (-pair[0], *_candidate_order(pair[1])[1:]))
    return [_result(item, score) for score, item in fused[:limit]]


def _prior_adjustment(item: _ScoredSection, predictions: Mapping[str, float]) -> float:
    """Bounded rerank among hits that already matched. Missing labels add nothing."""
    if not predictions:
        return 0.0
    labels = cfr_labels(item.policy.cfr_references)
    if not labels:
        return 0.0
    return PRIOR_WEIGHT * max(predictions.get(label, 0.0) for label in labels)


def _adjusted_order(
    item: _ScoredSection,
    score: float,
    predictions: Mapping[str, float],
) -> tuple[float, str, int, int, str]:
    adjusted = score + _prior_adjustment(item, predictions)
    return (
        -adjusted,
        item.policy.name,
        -item.policy.version,
        item.section.position,
        item.section.section_ref,
    )


def _candidate_order(item: _ScoredSection) -> tuple[float, str, int, int, str]:
    return (
        -item.lexical_score,
        item.policy.name,
        -item.policy.version,
        item.section.position,
        item.section.section_ref,
    )


def _result(item: _ScoredSection, score: float) -> RetrievedPolicy:
    return RetrievedPolicy(
        id=item.policy.id,
        name=item.policy.name,
        version=item.policy.version,
        section_ref=item.section.section_ref,
        heading=item.section.heading,
        content=item.section.text,
        position=item.section.position,
        score=score,
        lexical_score=item.lexical_score,
        embedding_score=item.embedding_score,
        section_id=item.section.id if isinstance(item.section, PolicySection) else "",
        document_number=item.policy.document_number,
    )
