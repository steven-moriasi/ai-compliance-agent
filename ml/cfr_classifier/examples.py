"""Documents and the publication-date split used by every compared model."""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class Example:
    document_number: str
    publication_date: date
    text: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Split:
    train: tuple[Example, ...]
    test: tuple[Example, ...]
    cutoff: date


def document_text(title: str | None, content: str, *, limit: int) -> str:
    """Title plus a capped body. Full rule text is too long to vectorize on this host."""
    head = (title or "").strip()
    body = content[:limit]
    if head and body:
        return f"{head}\n{body}"
    return head or body


def term_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in TOKEN_PATTERN.findall(text.lower()):
        counts[token] = counts.get(token, 0) + 1
    return counts


def time_split(examples: Sequence[Example], cutoff: date) -> Split:
    """Train on documents published before the cutoff. The cutoff day is test."""
    train = tuple(item for item in examples if item.publication_date < cutoff)
    test = tuple(item for item in examples if item.publication_date >= cutoff)
    return Split(train=train, test=test, cutoff=cutoff)


def labels_at_support(train: Sequence[Example], min_support: int) -> tuple[str, ...]:
    """Keep labels the training period actually repeats. Support is not counted on test."""
    counts = Counter(label for item in train for label in item.labels)
    return tuple(sorted(label for label, count in counts.items() if count >= min_support))


def restrict_labels(examples: Sequence[Example], kept: Sequence[str]) -> tuple[Example, ...]:
    """Drop labels the comparison cannot score, and drop documents that then have none."""
    allowed = set(kept)
    restricted: list[Example] = []
    for item in examples:
        labels = tuple(label for label in item.labels if label in allowed)
        if labels:
            restricted.append(replace(item, labels=labels))
    return tuple(restricted)


def label_support(examples: Sequence[Example]) -> Mapping[str, int]:
    return dict(Counter(label for item in examples for label in item.labels))
