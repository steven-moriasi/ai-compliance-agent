"""Score the four CFR-part rankings on one frozen split."""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.services.classifier import (
    DRIFT_THRESHOLD,
    cfr_part_drift,
    fit_cfr_prior,
    predict_cfr_parts,
)
from ml.cfr_classifier.drift import PcaShift, pca_mean_shift
from ml.cfr_classifier.examples import (
    Example,
    label_support,
    labels_at_support,
    restrict_labels,
    term_counts,
    time_split,
)
from ml.cfr_classifier.metrics import (
    majority_rankings,
    multi_hot,
    rank_scores,
    rankings_from_scores,
    recall_at_k,
    top1_hit_rate,
)
from ml.cfr_classifier.promote import PromotionDecision, decide
from ml.cfr_classifier.tfidf import fit_tfidf, transform

ScoreTrainer = Callable[..., list[list[float]]]
RECALL_K = 3


class InsufficientCorpus(Exception):
    """The date split or the training vocabulary cannot support a comparison."""


@dataclass(frozen=True)
class ModelScore:
    top1_hit_rate: float
    recall_at_3: float
    elapsed_seconds: float

    def to_dict(self) -> dict[str, float]:
        return {
            "top1_hit_rate": self.top1_hit_rate,
            "recall_at_3": self.recall_at_3,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True)
class Comparison:
    status: str
    cutoff: date
    min_label_support: int
    max_terms: int
    min_df: int
    train_documents: int
    test_documents: int
    dropped_train_documents: int
    dropped_test_documents: int
    labels: tuple[str, ...]
    label_support_train: Mapping[str, int]
    label_support_test: Mapping[str, int]
    vocabulary_size: int
    models: Mapping[str, ModelScore]
    gold_population_stability: float
    gold_drifted: bool
    pca: PcaShift
    promotion: PromotionDecision

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "cutoff": self.cutoff.isoformat(),
            "min_label_support": self.min_label_support,
            "max_terms": self.max_terms,
            "min_df": self.min_df,
            "train_documents": self.train_documents,
            "test_documents": self.test_documents,
            "dropped_train_documents": self.dropped_train_documents,
            "dropped_test_documents": self.dropped_test_documents,
            "labels": list(self.labels),
            "label_support_train": dict(self.label_support_train),
            "label_support_test": dict(self.label_support_test),
            "vocabulary_size": self.vocabulary_size,
            "models": {name: score.to_dict() for name, score in self.models.items()},
            "drift": {
                "gold_population_stability": self.gold_population_stability,
                "gold_drifted": self.gold_drifted,
                "threshold": DRIFT_THRESHOLD,
                "pca": {
                    "components": self.pca.components,
                    "power_iterations": self.pca.power_iterations,
                    "l2_mean_shift": self.pca.l2_mean_shift,
                    "component_shifts": list(self.pca.component_shifts),
                    "explained_variance_ratio": list(self.pca.explained_variance_ratio),
                },
            },
            "promotion": self.promotion.to_dict(),
        }


def run_comparison(
    examples: Sequence[Example],
    *,
    cutoff: date,
    min_label_support: int,
    max_terms: int,
    min_df: int,
    trainer: ScoreTrainer,
    pca_components: int = 2,
) -> Comparison:
    """Fit every model on the earlier window and score the later window once."""
    split = time_split(examples, cutoff)
    kept = labels_at_support(split.train, min_label_support)
    train = restrict_labels(split.train, kept)
    test = restrict_labels(split.test, kept)
    if not train or not test or not kept:
        raise InsufficientCorpus("the time split has no trainable label on both sides")
    train_counts = [term_counts(item.text) for item in train]
    test_counts = [term_counts(item.text) for item in test]
    index = fit_tfidf(train_counts, max_terms=max_terms, min_df=min_df)
    if not index.vocabulary:
        raise InsufficientCorpus("tf-idf vocabulary is empty")
    train_vectors = transform(index, train_counts)
    test_vectors = transform(index, test_counts)
    gold = [item.labels for item in test]
    started = time.perf_counter()
    majority = majority_rankings(train, test)
    majority_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    bayes = _naive_bayes_rankings(train, test)
    bayes_elapsed = time.perf_counter() - started
    train_y = multi_hot(train, kept)
    linear_scores, linear_elapsed = _timed(
        trainer, train_vectors, train_y, test_vectors, kind="linear"
    )
    mlp_scores, mlp_elapsed = _timed(trainer, train_vectors, train_y, test_vectors, kind="mlp")
    models = {
        "majority": _score(majority, gold, majority_elapsed),
        "naive_bayes": _score(bayes, gold, bayes_elapsed),
        "tfidf_logreg": _score(rankings_from_scores(linear_scores, kept), gold, linear_elapsed),
        "mlp": _score(rankings_from_scores(mlp_scores, kept), gold, mlp_elapsed),
    }
    drift = cfr_part_drift(label_support(train), label_support(test))
    pca = pca_mean_shift(train_vectors, test_vectors, components=pca_components)
    comparison = Comparison(
        status="scored",
        cutoff=cutoff,
        min_label_support=min_label_support,
        max_terms=max_terms,
        min_df=min_df,
        train_documents=len(train),
        test_documents=len(test),
        dropped_train_documents=len(split.train) - len(train),
        dropped_test_documents=len(split.test) - len(test),
        labels=kept,
        label_support_train=label_support(train),
        label_support_test=label_support(test),
        vocabulary_size=len(index.vocabulary),
        models=models,
        gold_population_stability=drift.population_stability,
        gold_drifted=drift.drifted,
        pca=pca,
        promotion=PromotionDecision(False, "mlp", "naive_bayes", ()),
    )
    decision = decide(comparison.to_dict())
    return Comparison(
        status=comparison.status,
        cutoff=comparison.cutoff,
        min_label_support=comparison.min_label_support,
        max_terms=comparison.max_terms,
        min_df=comparison.min_df,
        train_documents=comparison.train_documents,
        test_documents=comparison.test_documents,
        dropped_train_documents=comparison.dropped_train_documents,
        dropped_test_documents=comparison.dropped_test_documents,
        labels=comparison.labels,
        label_support_train=comparison.label_support_train,
        label_support_test=comparison.label_support_test,
        vocabulary_size=comparison.vocabulary_size,
        models=comparison.models,
        gold_population_stability=comparison.gold_population_stability,
        gold_drifted=comparison.gold_drifted,
        pca=comparison.pca,
        promotion=decision,
    )


def _naive_bayes_rankings(train: Sequence[Example], test: Sequence[Example]) -> list[list[str]]:
    prior = fit_cfr_prior([(item.text, label) for item in train for label in item.labels])
    return [rank_scores(predict_cfr_parts(prior, item.text)) for item in test]


def _score(
    rankings: Sequence[Sequence[str]],
    gold: Sequence[Sequence[str]],
    elapsed: float,
) -> ModelScore:
    return ModelScore(
        top1_hit_rate=top1_hit_rate(rankings, gold),
        recall_at_3=recall_at_k(rankings, gold, k=RECALL_K),
        elapsed_seconds=elapsed,
    )


def _timed(
    trainer: ScoreTrainer,
    train_x: Sequence[Sequence[float]],
    train_y: Sequence[Sequence[int]],
    test_x: Sequence[Sequence[float]],
    *,
    kind: str,
) -> tuple[list[list[float]], float]:
    started = time.perf_counter()
    scores = trainer(train_x, train_y, test_x, kind=kind)
    return scores, time.perf_counter() - started

