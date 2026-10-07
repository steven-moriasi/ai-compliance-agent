"""PostgreSQL vector and hybrid retrieval.

The query is embedded once. Candidates come from an HNSW index, not from a
Python scan of every section. Hybrid fusion uses the same reciprocal-rank
constant as the in-memory path.
"""

from collections.abc import Mapping, Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.services.embeddings import Embedder, RetrievalUnavailable
from app.services.fulltext import qualified_table, retrieve_fulltext
from app.services.retrieval import RRF_K, RetrievedPolicy

_WIDTH = 384
_CANDIDATES = 100


def vector_literal(values: Sequence[float]) -> str:
    """Format a vector for a bound pgvector cast. Values are not identifiers."""
    if len(values) != _WIDTH:
        raise RetrievalUnavailable(f"expected {_WIDTH} dimensions, got {len(values)}")
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _copy(
    item: RetrievedPolicy,
    *,
    score: float | None = None,
    embedding_score: float | None = None,
) -> RetrievedPolicy:
    return RetrievedPolicy(
        id=item.id,
        name=item.name,
        version=item.version,
        section_ref=item.section_ref,
        heading=item.heading,
        content=item.content,
        position=item.position,
        score=item.score if score is None else score,
        lexical_score=item.lexical_score,
        embedding_score=item.embedding_score if embedding_score is None else embedding_score,
        section_id=item.section_id,
        document_number=item.document_number,
    )


def fuse_ranks(
    lexical: Sequence[RetrievedPolicy],
    semantic: Sequence[RetrievedPolicy],
    limit: int,
) -> list[RetrievedPolicy]:
    """Reciprocal rank fusion. A list that did not contain a section contributes nothing."""
    scores: dict[tuple[str, str, int], float] = {}
    chosen: dict[tuple[str, str, int], RetrievedPolicy] = {}
    for source, items in (("lexical", lexical), ("semantic", semantic)):
        for rank, item in enumerate(items, start=1):
            key = (item.id, item.section_ref, item.position)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            current = chosen.get(key)
            if current is None:
                chosen[key] = item
                continue
            if source == "semantic":
                chosen[key] = _copy(current, embedding_score=item.embedding_score)
            else:
                chosen[key] = _copy(item, embedding_score=current.embedding_score)
    ranked = sorted(
        chosen,
        key=lambda key: (
            -scores[key],
            chosen[key].name,
            -chosen[key].version,
            chosen[key].position,
            chosen[key].section_ref,
        ),
    )
    return [_copy(chosen[key], score=scores[key]) for key in ranked[:limit]]


def retrieve_semantic(
    session: Session,
    document: str,
    case_date: date,
    limit: int,
    predictions: Mapping[str, float],
    *,
    mode: str,
    embedder: Embedder | None,
    model_id: str,
) -> list[RetrievedPolicy]:
    """Vector search, or hybrid fusion with full text. Raises when the index cannot be used."""
    if mode == "hybrid":
        lexical = retrieve_fulltext(session, document, case_date, _CANDIDATES, predictions)
        semantic = _vector_hits(session, document, case_date, _CANDIDATES, embedder, model_id)
        return fuse_ranks(lexical, semantic, limit)
    return _vector_hits(session, document, case_date, limit, embedder, model_id)


def _vector_hits(
    session: Session,
    document: str,
    case_date: date,
    limit: int,
    embedder: Embedder | None,
    model_id: str,
) -> list[RetrievedPolicy]:
    if embedder is None:
        raise RetrievalUnavailable("vector search needs an embedder")
    vectors = embedder.embed([document])
    if len(vectors) != 1:
        raise RetrievalUnavailable("embedder did not return a query vector")
    literal = vector_literal(vectors[0])
    statement = text(_sql(session))
    try:
        rows = session.execute(
            statement,
            {
                "model_id": model_id,
                "query": literal,
                "case_date": case_date,
                "limit": limit,
            },
        ).mappings()
        fetched = list(rows)
    except ProgrammingError as exc:
        session.rollback()
        raise RetrievalUnavailable("section embeddings are not available") from exc
    if not fetched and not _model_present(session, model_id):
        raise RetrievalUnavailable("section embeddings are not built for this model")
    ranked: list[RetrievedPolicy] = []
    for row in fetched:
        distance = float(row["distance"] or 0.0)
        similarity = 1.0 - distance
        section_id = row.get("section_id")
        document_number = row.get("document_number")
        ranked.append(
            RetrievedPolicy(
                id=row["policy_id"],
                name=row["name"],
                version=int(row["version"]),
                section_ref=row["section_ref"],
                heading=row["heading"],
                content=row["content"],
                position=int(row["position"]),
                score=similarity,
                embedding_score=similarity,
                section_id=section_id if isinstance(section_id, str) else "",
                document_number=document_number if isinstance(document_number, str) else None,
            )
        )
    return ranked


def _model_present(session: Session, model_id: str) -> bool:
    table = qualified_table(session, "section_embeddings")
    statement = text(f"SELECT 1 FROM {table} WHERE model_id = :model_id LIMIT 1")  # noqa: S608
    try:
        return session.execute(statement, {"model_id": model_id}).first() is not None
    except ProgrammingError:
        session.rollback()
        return False


def _sql(session: Session) -> str:
    policies = qualified_table(session, "policies")
    sections = qualified_table(session, "policy_sections")
    embeddings = qualified_table(session, "section_embeddings")
    return _VECTOR_SQL.format(
        policies=policies,
        sections=sections,
        embeddings=embeddings,
    )


_VECTOR_SQL = """
    SELECT
        p.id AS policy_id,
        p.name AS name,
        p.version AS version,
        s.section_ref AS section_ref,
        s.heading AS heading,
        s.text AS content,
        s.position AS position,
        s.id AS section_id,
        p.document_number AS document_number,
        e.embedding <=> CAST(:query AS vector) AS distance
    FROM {embeddings} AS e
    JOIN {sections} AS s ON s.id = e.section_id
    JOIN {policies} AS p ON p.id = s.policy_id
    WHERE e.model_id = :model_id
      AND p.status = 'ACTIVE'
      AND (p.effective_from IS NULL OR p.effective_from <= :case_date)
      AND (p.effective_to IS NULL OR p.effective_to > :case_date)
    ORDER BY e.embedding <=> CAST(:query AS vector)
    LIMIT :limit
"""
