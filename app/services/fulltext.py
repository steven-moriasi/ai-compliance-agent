"""PostgreSQL full-text retrieval.

The keyword path scores every section in Python. This path keeps that work in
SQL: status, effective dates, and the match all run before any row is returned.
"""

import re
from collections.abc import Mapping
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ingestion.federal_register.synonyms import synonym_phrases
from app.services.classifier import cfr_labels
from app.services.embeddings import RetrievalUnavailable
from app.services.retrieval import PRIOR_WEIGHT, RetrievedPolicy

_CANDIDATE_LIMIT = 100


def websearch_query(value: str) -> str:
    """Turn a user query into websearch syntax, adding synonym phrases as OR terms.

    A phrase stays quoted. Splitting "nitrogen oxides" into separate words would
    match almost every air rule and throw away the index.
    """
    folded = re.sub(r"\s+", " ", value.lower()).strip()
    extra = sorted(form for form in synonym_phrases(value) if form not in folded)
    if not extra:
        return folded
    return folded + " OR " + " OR ".join(_websearch_term(form) for form in extra)


def _websearch_term(form: str) -> str:
    if " " in form:
        return f'"{form}"'
    return form


def retrieve_fulltext(
    session: Session,
    document: str,
    case_date: date,
    limit: int,
    predictions: Mapping[str, float],
) -> list[RetrievedPolicy]:
    """Return the top matches from the stored tsvector. The caller already chose PostgreSQL."""
    statement = text(_sql(session))
    rows = session.execute(
        statement,
        {
            "query": websearch_query(document),
            "case_date": case_date,
            "candidate_limit": min(_CANDIDATE_LIMIT, max(limit, 1) * 20),
        },
    ).mappings()
    ranked: list[RetrievedPolicy] = []
    for row in rows:
        lexical = float(row["rank"] or 0.0)
        bonus = _prior_bonus(row["cfr_references"], predictions)
        ranked.append(
            RetrievedPolicy(
                id=row["policy_id"],
                name=row["name"],
                version=int(row["version"]),
                section_ref=row["section_ref"],
                heading=row["heading"],
                content=row["content"],
                position=int(row["position"]),
                score=lexical + bonus,
                lexical_score=lexical,
                section_id=_text(row.get("section_id")),
                document_number=_optional_text(row.get("document_number")),
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.score,
            item.name,
            -item.version,
            item.position,
            item.section_ref,
        )
    )
    return ranked[:limit]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _prior_bonus(references: object, predictions: Mapping[str, float]) -> float:
    if not predictions:
        return 0.0
    labels = cfr_labels(references)
    if not labels:
        return 0.0
    return PRIOR_WEIGHT * max(predictions.get(label, 0.0) for label in labels)


_FULLTEXT_SQL = """
    WITH query AS (
        SELECT websearch_to_tsquery('english', :query) AS tsq
    ),
    sectionless AS MATERIALIZED (
        SELECT
            policy.id,
            policy.name,
            policy.version,
            policy.content,
            policy.cfr_references,
            policy.document_number
        FROM {policies} AS policy
        WHERE policy.status = 'ACTIVE'
          AND (policy.effective_from IS NULL OR policy.effective_from <= :case_date)
          AND (policy.effective_to IS NULL OR policy.effective_to > :case_date)
          AND NOT EXISTS (
                SELECT 1 FROM {sections} AS sectionless
                WHERE sectionless.policy_id = policy.id
          )
    )
    SELECT *
    FROM (
        SELECT
            p.id AS policy_id,
            p.name AS name,
            p.version AS version,
            s.section_ref AS section_ref,
            s.heading AS heading,
            s.text AS content,
            s.position AS position,
            p.cfr_references AS cfr_references,
            s.id AS section_id,
            p.document_number AS document_number,
            ts_rank_cd(s.search_vector, query.tsq) AS rank
        FROM {sections} AS s
        JOIN {policies} AS p ON p.id = s.policy_id
        CROSS JOIN query
        WHERE p.status = 'ACTIVE'
          AND (p.effective_from IS NULL OR p.effective_from <= :case_date)
          AND (p.effective_to IS NULL OR p.effective_to > :case_date)
          AND s.search_vector @@ query.tsq
        UNION ALL
        SELECT
            p.id,
            p.name,
            p.version,
            'document',
            p.name,
            p.content,
            0,
            p.cfr_references,
            NULL,
            p.document_number,
            ts_rank_cd(to_tsvector('english', coalesce(p.content, '')), query.tsq)
        FROM sectionless AS p
        CROSS JOIN query
        WHERE to_tsvector('english', coalesce(p.content, '')) @@ query.tsq
    ) AS hits
    ORDER BY rank DESC, name ASC, version DESC, position ASC, section_ref ASC
    LIMIT :candidate_limit
"""


def _sql(session: Session) -> str:
    policies = qualified_table(session, "policies")
    sections = qualified_table(session, "policy_sections")
    # Identifiers are fixed names, or a schema that already matched a safe pattern.
    return _FULLTEXT_SQL.format(policies=policies, sections=sections)


def qualified_table(session: Session, name: str) -> str:
    bind = session.get_bind()
    options = bind.get_execution_options() if bind is not None else {}
    schema = (options.get("schema_translate_map") or {}).get(None)
    if isinstance(schema, str) and schema:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise RetrievalUnavailable("schema name is not a safe identifier")
        return f'"{schema}".{name}'
    return name
