"""Train-only TF-IDF. Test documents cannot add terms or change the inverse document frequency."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TfidfIndex:
    vocabulary: tuple[str, ...]
    idf: tuple[float, ...]


def fit_tfidf(
    documents: Sequence[Mapping[str, int]],
    *,
    max_terms: int,
    min_df: int,
) -> TfidfIndex:
    """Keep the most common training terms. Inverse document frequency uses the training count."""
    document_frequency: dict[str, int] = {}
    for document in documents:
        for term, count in document.items():
            if count > 0:
                document_frequency[term] = document_frequency.get(term, 0) + 1
    kept = [term for term, count in document_frequency.items() if count >= min_df]
    kept.sort(key=lambda term: (-document_frequency[term], term))
    kept = kept[:max_terms]
    documents_count = len(documents)
    idf = tuple(
        math.log((1 + documents_count) / (1 + document_frequency[term])) + 1 for term in kept
    )
    return TfidfIndex(vocabulary=tuple(kept), idf=idf)


def transform(
    index: TfidfIndex,
    documents: Sequence[Mapping[str, int]],
) -> list[list[float]]:
    """Sublinear term frequency, then L2 length so a long rule does not dominate the dot product."""
    positions = {term: index_ for index_, term in enumerate(index.vocabulary)}
    rows: list[list[float]] = []
    for document in documents:
        row = [0.0] * len(index.vocabulary)
        for term, count in document.items():
            position = positions.get(term)
            if position is None or count <= 0:
                continue
            row[position] = (1.0 + math.log(count)) * index.idf[position]
        rows.append(_l2_normalize(row))
    return rows


def _l2_normalize(row: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in row))
    if norm == 0:
        return row
    return [value / norm for value in row]
