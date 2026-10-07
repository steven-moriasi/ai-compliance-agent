"""Decide whether a trained MLP artifact may be kept. This does not review a case."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.services.classifier import DRIFT_THRESHOLD


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    candidate: str
    baseline: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "promoted": self.promoted,
            "candidate": self.candidate,
            "baseline": self.baseline,
            "reasons": list(self.reasons),
        }


def decide(report: Mapping[str, object]) -> PromotionDecision:
    """Promote the MLP only when it matches the simpler models and the label mix has not shifted.

    The candidate has to meet the naive Bayes baseline, the TF-IDF logistic
    regression, and the majority-class ranking. A population-stability index
    above the existing 0.25 cutoff also refuses promotion. Refusal is a valid
    measurement.
    """
    candidate = "mlp"
    baseline = "naive_bayes"
    if report.get("status") != "scored":
        return PromotionDecision(
            promoted=False,
            candidate=candidate,
            baseline=baseline,
            reasons=("the comparison did not score a held-out window",),
        )
    models = _models(report.get("models"))
    drift = report.get("drift")
    reasons: list[str] = []
    promoted = True
    promoted = _require_at_least(models, candidate, baseline, reasons) and promoted
    promoted = _require_at_least(models, candidate, "tfidf_logreg", reasons) and promoted
    promoted = _require_at_least(models, candidate, "majority", reasons) and promoted
    promoted = _require_stable(drift, reasons) and promoted
    if promoted:
        reasons.append(
            "mlp matches or exceeds majority, naive Bayes, and TF-IDF logistic regression, "
            "and the held-out label mix is within the stability cutoff"
        )
    return PromotionDecision(
        promoted=promoted,
        candidate=candidate,
        baseline=baseline,
        reasons=tuple(reasons),
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Refuse a CFR MLP artifact that misses its gates")
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("report is not an object", file=sys.stderr)
        return 2
    decision = decide(payload)
    print(json.dumps(decision.to_dict(), sort_keys=True))
    return 0 if decision.promoted else 1


def _require_at_least(
    models: Mapping[str, Mapping[str, float]],
    candidate: str,
    other: str,
    reasons: list[str],
) -> bool:
    left = models.get(candidate)
    right = models.get(other)
    if left is None or right is None:
        reasons.append(f"missing scores for {candidate} or {other}")
        return False
    passed = True
    for metric in ("top1_hit_rate", "recall_at_3"):
        if left[metric] < right[metric]:
            reasons.append(f"{candidate} {metric} {left[metric]} is below {other} {right[metric]}")
            passed = False
    return passed


def _require_stable(drift: object, reasons: list[str]) -> bool:
    if not isinstance(drift, dict):
        reasons.append("missing drift section")
        return False
    stability = drift.get("gold_population_stability")
    threshold = drift.get("threshold", DRIFT_THRESHOLD)
    if not isinstance(stability, int | float) or isinstance(stability, bool):
        reasons.append("missing gold population stability")
        return False
    if not isinstance(threshold, int | float) or isinstance(threshold, bool):
        reasons.append("missing drift threshold")
        return False
    if float(stability) > float(threshold):
        reasons.append(
            f"gold population stability {stability} is above the cutoff {threshold}"
        )
        return False
    return True


def _models(value: object) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict):
        return {}
    models: dict[str, dict[str, float]] = {}
    for name, scores in value.items():
        if not isinstance(name, str) or not isinstance(scores, dict):
            continue
        parsed: dict[str, float] = {}
        for metric in ("top1_hit_rate", "recall_at_3"):
            metric_value = scores.get(metric)
            if isinstance(metric_value, int | float) and not isinstance(metric_value, bool):
                parsed[metric] = float(metric_value)
        if len(parsed) == 2:
            models[name] = parsed
    return models


if __name__ == "__main__":
    raise SystemExit(main())
