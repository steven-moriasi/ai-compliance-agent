"""Ranking metrics shared by the majority class, naive Bayes, and both torch heads."""

from collections import Counter
from collections.abc import Mapping, Sequence

from ml.cfr_classifier.examples import Example


def majority_rankings(train: Sequence[Example], test: Sequence[Example]) -> list[list[str]]:
    """Same frequency order for every document. This is the trivial baseline."""
    counts = Counter(label for item in train for label in item.labels)
    order = sorted(counts, key=lambda label: (-counts[label], label))
    return [order for _item in test]


def rankings_from_scores(
    scores: Sequence[Sequence[float]],
    labels: Sequence[str],
) -> list[list[str]]:
    rankings: list[list[str]] = []
    for row in scores:
        order = sorted(range(len(labels)), key=lambda index: (-row[index], labels[index]))
        rankings.append([labels[index] for index in order])
    return rankings


def top1_hit_rate(rankings: Sequence[Sequence[str]], gold: Sequence[Sequence[str]]) -> float:
    """Share of documents whose first prediction is one of the document's CFR parts."""
    if not gold:
        return 0.0
    hits = sum(
        1 for ranked, labels in zip(rankings, gold, strict=True) if ranked and ranked[0] in labels
    )
    return hits / len(gold)


def recall_at_k(
    rankings: Sequence[Sequence[str]],
    gold: Sequence[Sequence[str]],
    *,
    k: int,
) -> float:
    """Mean fraction of a document's labels found in the first k predictions."""
    if not gold:
        return 0.0
    total = 0.0
    for ranked, labels in zip(rankings, gold, strict=True):
        if not labels:
            continue
        found = len(set(ranked[:k]) & set(labels))
        total += found / len(labels)
    return total / len(gold)


def multi_hot(examples: Sequence[Example], labels: Sequence[str]) -> list[list[int]]:
    index = {label: position for position, label in enumerate(labels)}
    rows: list[list[int]] = []
    for item in examples:
        row = [0] * len(labels)
        for label in item.labels:
            row[index[label]] = 1
        rows.append(row)
    return rows


def rank_scores(scores: Mapping[str, float]) -> list[str]:
    return sorted(scores, key=lambda label: (-scores[label], label))
