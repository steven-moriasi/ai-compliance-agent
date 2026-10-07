"""Ranking metrics for one retrieval set.

A negative item has no gold section and no gold document. It is left out of
Recall, MRR, and nDCG. Returning any hit for that item is counted on its own,
because "nothing relevant" is not a rank.
"""

import math
from collections.abc import Sequence


def relevant(
    section_id: str,
    document_number: str | None,
    gold_sections: set[str],
    gold_documents: set[str],
) -> bool:
    """Gold section ids win. Document numbers apply only when no section was named."""
    if gold_sections:
        return section_id in gold_sections
    if gold_documents:
        return bool(document_number and document_number in gold_documents)
    return False


def recall_at(hits: Sequence[bool], k: int) -> float:
    return 1.0 if any(hits[:k]) else 0.0


def mrr_at(hits: Sequence[bool], k: int) -> float:
    for index, hit in enumerate(hits[:k], start=1):
        if hit:
            return 1.0 / index
    return 0.0


def ndcg_at(hits: Sequence[bool], gold_count: int, k: int) -> float:
    """Binary nDCG. The ideal list has one gain per gold item, capped at k."""
    gains = [1.0 if hit else 0.0 for hit in list(hits[:k]) + [False] * k][:k]
    ideal_count = min(k, max(gold_count, 0))
    ideal = [1.0] * ideal_count + [0.0] * (k - ideal_count)
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0.0:
        return 0.0
    return _dcg(gains) / ideal_dcg


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _dcg(gains: Sequence[float]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
