import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.models import Policy
from ml.cfr_classifier.compare import InsufficientCorpus, run_comparison
from ml.cfr_classifier.drift import pca_mean_shift
from ml.cfr_classifier.examples import (
    Example,
    document_text,
    labels_at_support,
    restrict_labels,
    term_counts,
    time_split,
)
from ml.cfr_classifier.metrics import recall_at_k, top1_hit_rate
from ml.cfr_classifier.promote import decide, main
from ml.cfr_classifier.tfidf import fit_tfidf, transform
from ml.cfr_classifier.train import (
    comparison_command,
    load_corpus,
    public_database_url,
    redact_command,
)

CUTOFF = date(2024, 7, 1)


def test_time_split_keeps_the_cutoff_day_for_test() -> None:
    examples = (
        _example("early", date(2024, 6, 30), "stack", ("40:60",)),
        _example("cutoff", date(2024, 7, 1), "ozone", ("40:52",)),
    )

    split = time_split(examples, CUTOFF)

    assert [item.document_number for item in split.train] == ["early"]
    assert [item.document_number for item in split.test] == ["cutoff"]


def test_label_support_is_counted_on_train_only() -> None:
    train = (
        _example("one", date(2024, 1, 1), "stack", ("40:60",)),
        _example("two", date(2024, 1, 2), "stack", ("40:60",)),
    )
    test = (_example("later", date(2024, 8, 1), "rare part", ("40:99",)),)

    kept = labels_at_support(train, 2)
    restricted = restrict_labels((*train, *test), kept)

    assert kept == ("40:60",)
    assert [item.document_number for item in restricted] == ["one", "two"]


def test_tfidf_document_frequency_ignores_repeats_inside_one_document() -> None:
    index = fit_tfidf(
        ({"stack": 9, "onlytrain": 4}, {"stack": 2}),
        max_terms=10,
        min_df=1,
    )
    rows = transform(index, ({"stack": 1, "onlytest": 3},))

    assert index.vocabulary[0] == "stack"
    assert "onlytest" not in index.vocabulary
    assert rows[0][index.vocabulary.index("stack")] > 0
    assert sum(rows[0]) > 0


def test_ranking_metrics_use_the_gold_set() -> None:
    rankings = (["40:52", "40:60"], ["40:60"])
    gold = (("40:60", "40:52"), ("40:63",))

    assert top1_hit_rate(rankings, gold) == pytest.approx(0.5)
    assert recall_at_k(rankings, gold, k=3) == pytest.approx(0.5)


def test_pca_shift_is_zero_on_identical_rows_and_positive_when_the_mean_moves() -> None:
    same = pca_mean_shift([[1.0, 0.0], [1.0, 0.0]], [[1.0, 0.0], [1.0, 0.0]], components=1)
    moved = pca_mean_shift([[1.0, 0.0], [1.0, 0.0]], [[0.0, 1.0], [0.0, 1.0]], components=1)

    assert same.l2_mean_shift == pytest.approx(0)
    assert moved.l2_mean_shift == pytest.approx(2**0.5)
    assert moved.component_shifts


def test_promotion_refuses_a_model_that_loses_to_naive_bayes_or_drifts() -> None:
    tied = _report(mlp_top1=0.8, bayes_top1=0.8, stability=0.1)
    weaker = _report(mlp_top1=0.2, bayes_top1=0.8, stability=0.1)
    drifted = _report(mlp_top1=0.9, bayes_top1=0.8, stability=0.5)

    assert decide(tied).promoted is True
    assert decide(weaker).promoted is False
    assert any("naive_bayes" in reason for reason in decide(weaker).reasons)
    assert decide(drifted).promoted is False
    assert decide({"status": "insufficient"}).promoted is False


def test_promote_command_exits_when_the_gate_refuses(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    body = json.dumps(_report(mlp_top1=0.2, bayes_top1=0.9, stability=0.0))
    report.write_text(body, encoding="utf-8")

    assert main([str(report)]) == 1


def test_comparison_uses_naive_bayes_and_refuses_a_constant_torch_head() -> None:
    examples = (
        _example("a", date(2024, 1, 1), "nitrogen oxides stack standard", ("40:60",)),
        _example("b", date(2024, 1, 2), "nitrogen oxides stack standard", ("40:60",)),
        _example("c", date(2024, 2, 1), "ozone implementation transport region", ("40:52",)),
        _example("d", date(2024, 2, 2), "ozone implementation transport region", ("40:52",)),
        _example("e", date(2024, 8, 1), "nitrogen oxides stack standard", ("40:60",)),
        _example("f", date(2024, 8, 2), "ozone implementation transport region", ("40:52",)),
    )
    calls: list[str] = []

    def trainer(
        train_x: list[list[float]],
        train_y: list[list[int]],
        test_x: list[list[float]],
        *,
        kind: str,
    ) -> list[list[float]]:
        calls.append(kind)
        return [[0.5, 0.5] for _row in test_x]

    comparison = run_comparison(
        examples,
        cutoff=CUTOFF,
        min_label_support=2,
        max_terms=20,
        min_df=1,
        trainer=trainer,
        pca_components=1,
    )

    assert calls == ["linear", "mlp"]
    assert comparison.models["naive_bayes"].top1_hit_rate == pytest.approx(1)
    assert comparison.promotion.promoted is False
    assert comparison.train_documents == 4
    assert comparison.test_documents == 2


def test_empty_split_is_not_scored() -> None:
    examples = (_example("only", date(2024, 1, 1), "stack standard", ("40:60",)),)

    with pytest.raises(InsufficientCorpus):
        run_comparison(
            examples,
            cutoff=CUTOFF,
            min_label_support=1,
            max_terms=10,
            min_df=1,
            trainer=lambda *_args, **_kwargs: [],
        )


def test_load_corpus_skips_a_policy_without_a_date(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add_all(
            (
                _policy("dated", date(2024, 1, 4), "Stack rule", "nitrogen oxides"),
                _policy("undated", None, "Missing date", "ozone"),
            )
        )
        session.commit()
        loaded = load_corpus(session, text_limit=20)

    assert [item.document_number for item in loaded.examples] == ["dated"]
    assert loaded.examples[0].text.startswith("Stack rule")
    assert loaded.skipped_without_date == 1
    assert loaded.dataset_versions == {"840a6be0a156": 2}


def test_recorded_command_drops_the_database_password() -> None:
    database_url = "postgresql+psycopg://compliance:compliance@127.0.0.1:5432/compliance"
    recorded = redact_command(
        [
            "py",
            "-3",
            "-m",
            "ml.cfr_classifier.train",
            "--database-url",
            database_url,
            "--cutoff",
            "2024-07-01",
        ]
    )

    assert "compliance:compliance" not in recorded
    assert "<redacted>" in recorded
    assert public_database_url(database_url) == "postgresql+psycopg://127.0.0.1:5432/compliance"
    command = comparison_command(
        cutoff="2024-07-01",
        min_label_support=5,
        text_limit=4000,
        max_terms=2000,
        min_df=3,
        epochs=40,
        hidden=64,
        learning_rate=0.001,
        weight_decay=0.0001,
        seed=7,
        artifact_dir=Path("data/models"),
        report=Path("evals/reports/cfr_classifier_2026-10-07.json"),
    )
    assert database_url not in command
    assert "--cutoff 2024-07-01" in command


def test_document_text_caps_the_body() -> None:
    assert document_text("Title", "abcdef", limit=3) == "Title\nabc"
    assert term_counts("NOx NOX stack")["nox"] == 2


def _example(number: str, published: date, text: str, labels: tuple[str, ...]) -> Example:
    return Example(number, published, text, labels)


def _report(*, mlp_top1: float, bayes_top1: float, stability: float) -> dict[str, object]:
    score = {"top1_hit_rate": mlp_top1, "recall_at_3": mlp_top1, "elapsed_seconds": 0.1}
    bayes = {"top1_hit_rate": bayes_top1, "recall_at_3": bayes_top1, "elapsed_seconds": 0.1}
    other = {"top1_hit_rate": mlp_top1, "recall_at_3": mlp_top1, "elapsed_seconds": 0.1}
    return {
        "status": "scored",
        "models": {
            "mlp": score,
            "naive_bayes": bayes,
            "tfidf_logreg": other,
            "majority": other,
        },
        "drift": {"gold_population_stability": stability, "threshold": 0.25},
    }


def _policy(policy_id: str, published: date | None, title: str, content: str) -> Policy:
    return Policy(
        id=policy_id,
        name=policy_id,
        version=1,
        status=PolicyStatus.ACTIVE,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title=title,
        document_number=policy_id,
        publication_date=published,
        cfr_references=[{"title": 40, "part": "60"}],
        dataset_version="840a6be0a156",
        created_by="test",
    )

