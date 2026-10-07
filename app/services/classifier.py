"""CFR-part prior and a population-stability check.

The prior only reranks policies that retrieval has already kept. Active status
and the effective-date window stay in retrieval.
"""

import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Policy
from app.ingestion.federal_register.synonyms import expand_tokens

TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")
DRIFT_THRESHOLD = 0.25
_SHARE_FLOOR = 1e-6


@dataclass(frozen=True)
class CfrPrior:
    label_token_counts: Mapping[str, Mapping[str, int]]
    label_totals: Mapping[str, int]
    document_counts: Mapping[str, int]
    vocabulary: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "label_token_counts": {
                label: dict(counts) for label, counts in self.label_token_counts.items()
            },
            "label_totals": dict(self.label_totals),
            "document_counts": dict(self.document_counts),
            "vocabulary": list(self.vocabulary),
        }


@dataclass(frozen=True)
class DriftReport:
    population_stability: float
    threshold: float
    drifted: bool


def fit_cfr_prior(examples: Sequence[tuple[str, str]]) -> CfrPrior:
    """Count tokens for each CFR label. One text may train more than one label."""
    token_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    documents: dict[str, int] = defaultdict(int)
    vocabulary: set[str] = set()
    for text, label in examples:
        cleaned = label.strip()
        tokens = _tokens(text)
        if not cleaned or not tokens:
            continue
        documents[cleaned] += 1
        for token in tokens:
            token_counts[cleaned][token] += 1
            totals[cleaned] += 1
            vocabulary.add(token)
    return CfrPrior(
        label_token_counts={label: dict(counts) for label, counts in token_counts.items()},
        label_totals=dict(totals),
        document_counts=dict(documents),
        vocabulary=tuple(sorted(vocabulary)),
    )


def fit_cfr_prior_from_policies(session: Session) -> CfrPrior:
    """Train on stored policy text. Callers still filter status and dates at retrieval."""
    examples: list[tuple[str, str]] = []
    for policy in session.scalars(select(Policy)).all():
        labels = cfr_labels(policy.cfr_references)
        if not labels:
            continue
        text = "\n".join(section.text for section in policy.sections) or policy.content
        examples.extend((text, label) for label in labels)
    return fit_cfr_prior(examples)


def predict_cfr_parts(prior: CfrPrior, text: str) -> dict[str, float]:
    """Return a probability for every trained CFR label."""
    if not prior.document_counts:
        return {}
    tokens = _tokens(text)
    vocab_size = max(len(prior.vocabulary), 1)
    total_docs = sum(prior.document_counts.values())
    log_scores: dict[str, float] = {}
    for label, doc_count in prior.document_counts.items():
        log_score = math.log(doc_count / total_docs)
        counts = prior.label_token_counts.get(label, {})
        total = prior.label_totals.get(label, 0)
        for token in tokens:
            log_score += math.log((counts.get(token, 0) + 1) / (total + vocab_size))
        log_scores[label] = log_score
    peak = max(log_scores.values())
    weights = {label: math.exp(score - peak) for label, score in log_scores.items()}
    normal = sum(weights.values()) or 1.0
    return {label: weight / normal for label, weight in weights.items()}


def cfr_labels(references: object) -> tuple[str, ...]:
    """Read `title:part` labels from Federal Register CFR reference objects."""
    if not isinstance(references, list):
        return ()
    labels: list[str] = []
    for item in references:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        part = item.get("part")
        if isinstance(title, bool) or not isinstance(title, int | str):
            continue
        if isinstance(part, bool) or not isinstance(part, int | str):
            continue
        part_text = str(part).strip()
        if not part_text:
            continue
        labels.append(f"{title}:{part_text}")
    return tuple(dict.fromkeys(labels))


def cfr_part_drift(
    reference: Mapping[str, int],
    current: Mapping[str, int],
    *,
    threshold: float = DRIFT_THRESHOLD,
) -> DriftReport:
    """Population stability index between two CFR-part count maps."""
    keys = tuple(sorted(set(reference) | set(current)))
    if not keys:
        return DriftReport(population_stability=0.0, threshold=threshold, drifted=False)
    reference_share = _shares(reference, keys)
    current_share = _shares(current, keys)
    stability = 0.0
    for key in keys:
        stability += (current_share[key] - reference_share[key]) * math.log(
            current_share[key] / reference_share[key]
        )
    return DriftReport(
        population_stability=stability,
        threshold=threshold,
        drifted=stability > threshold,
    )


def save_cfr_prior(path: Path, prior: CfrPrior) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prior.to_dict(), sort_keys=True), encoding="utf-8")


def load_cfr_prior(path: Path) -> CfrPrior | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return CfrPrior(
        label_token_counts=_count_maps(payload.get("label_token_counts")),
        label_totals=_count_map(payload.get("label_totals")),
        document_counts=_count_map(payload.get("document_counts")),
        vocabulary=tuple(
            token
            for token in payload.get("vocabulary", [])
            if isinstance(token, str)
        ),
    )


def _tokens(text: str) -> set[str]:
    return expand_tokens(text, set(TOKEN_PATTERN.findall(text.lower())))


def _shares(counts: Mapping[str, int], keys: Sequence[str]) -> dict[str, float]:
    total = float(sum(max(counts.get(key, 0), 0) for key in keys))
    if total == 0:
        raw = {key: 1.0 for key in keys}
    else:
        raw = {key: max(counts.get(key, 0), 0) / total for key in keys}
    floored = {key: max(value, _SHARE_FLOOR) for key, value in raw.items()}
    normal = sum(floored.values())
    return {key: value / normal for key, value in floored.items()}


def _count_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        if isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool):
            counts[key] = count
    return counts


def _count_maps(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    return {key: _count_map(counts) for key, counts in value.items() if isinstance(key, str)}
