"""Fit the CFR comparison and write the report. The database password is not recorded."""

import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Policy
from app.services.classifier import cfr_labels
from evals.host import git_sha, host_summary, recorded_at
from ml.cfr_classifier.compare import InsufficientCorpus, run_comparison
from ml.cfr_classifier.examples import Example, document_text
from ml.cfr_classifier.torch_train import fit_and_score, prepare_torch


@dataclass(frozen=True)
class LoadedCorpus:
    examples: tuple[Example, ...]
    dataset_versions: Mapping[str, int]
    skipped_without_date: int
    skipped_without_labels: int
    skipped_without_text: int


def load_corpus(session: Session, *, text_limit: int) -> LoadedCorpus:
    """One row per policy. Section bodies are not loaded; the text is title plus a body cap."""
    examples: list[Example] = []
    versions: Counter[str] = Counter()
    skipped_without_date = 0
    skipped_without_labels = 0
    skipped_without_text = 0
    for policy in session.scalars(select(Policy)).all():
        versions[policy.dataset_version or ""] += 1
        if policy.publication_date is None:
            skipped_without_date += 1
            continue
        labels = cfr_labels(policy.cfr_references)
        if not labels:
            skipped_without_labels += 1
            continue
        text = document_text(policy.title, policy.content, limit=text_limit)
        if not text.strip():
            skipped_without_text += 1
            continue
        examples.append(
            Example(
                document_number=policy.document_number or policy.id,
                publication_date=policy.publication_date,
                text=text,
                labels=labels,
            )
        )
    return LoadedCorpus(
        examples=tuple(examples),
        dataset_versions=dict(versions),
        skipped_without_date=skipped_without_date,
        skipped_without_labels=skipped_without_labels,
        skipped_without_text=skipped_without_text,
    )


def comparison_command(
    *,
    cutoff: str,
    min_label_support: int,
    text_limit: int,
    max_terms: int,
    min_df: int,
    epochs: int,
    hidden: int,
    learning_rate: float,
    weight_decay: float,
    seed: int,
    artifact_dir: Path,
    report: Path,
) -> str:
    """The shell line for this run. The database URL stays in the environment."""
    return (
        "py -3 -m ml.cfr_classifier.train "
        f"--cutoff {cutoff} "
        f"--min-label-support {min_label_support} "
        f"--text-limit {text_limit} "
        f"--max-terms {max_terms} "
        f"--min-df {min_df} "
        f"--epochs {epochs} "
        f"--hidden {hidden} "
        f"--learning-rate {learning_rate} "
        f"--weight-decay {weight_decay} "
        f"--seed {seed} "
        f"--artifact-dir {artifact_dir} "
        f"--report {report}"
    )


def public_database_url(url: str) -> str:
    """Drop userinfo so a report can name the database without storing the password."""
    parsed = urlsplit(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname or ''}{port}{parsed.path}"


def redact_command(argv: Sequence[str]) -> str:
    redacted: list[str] = []
    skip_next = False
    for argument in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        if argument == "--database-url":
            redacted.append(argument)
            skip_next = True
            continue
        if argument.startswith("--database-url="):
            redacted.append("--database-url=<redacted>")
            continue
        redacted.append(argument)
    return " ".join(redacted)


def write_report(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    parser = argparse.ArgumentParser(description="Compare CFR-part classifiers on a date split.")
    parser.add_argument("--database-url", default=os.environ.get("COMPLIANCE_DATABASE_URL", ""))
    parser.add_argument("--cutoff", default="2024-07-01")
    parser.add_argument("--min-label-support", type=int, default=5)
    parser.add_argument("--text-limit", type=int, default=4000)
    parser.add_argument("--max-terms", type=int, default=2000)
    parser.add_argument("--min-df", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--artifact-dir", type=Path, default=Path("data/models"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("evals/reports/cfr_classifier_2026-10-07.json"),
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        print("COMPLIANCE_DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    recorded_command = comparison_command(
        cutoff=args.cutoff,
        min_label_support=args.min_label_support,
        text_limit=args.text_limit,
        max_terms=args.max_terms,
        min_df=args.min_df,
        epochs=args.epochs,
        hidden=args.hidden,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
        artifact_dir=args.artifact_dir,
        report=args.report,
    )
    engine = create_engine(args.database_url)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        loaded = load_corpus(session, text_limit=args.text_limit)
    cutoff = date.fromisoformat(args.cutoff)
    linear_path = args.artifact_dir / "cfr_tfidf_logreg.safetensors"
    mlp_path = args.artifact_dir / "cfr_mlp.safetensors"

    def trainer(
        train_x: Sequence[Sequence[float]],
        train_y: Sequence[Sequence[int]],
        test_x: Sequence[Sequence[float]],
        *,
        kind: str,
    ) -> list[list[float]]:
        artifact = linear_path if kind == "linear" else mlp_path
        if kind == "linear":
            return fit_and_score(
                train_x,
                train_y,
                test_x,
                kind="linear",
                epochs=args.epochs,
                seed=args.seed,
                hidden=args.hidden,
                learning_rate=args.learning_rate,
                weight_decay=args.weight_decay,
                artifact_path=artifact,
            )
        if kind == "mlp":
            return fit_and_score(
                train_x,
                train_y,
                test_x,
                kind="mlp",
                epochs=args.epochs,
                seed=args.seed,
                hidden=args.hidden,
                learning_rate=args.learning_rate,
                weight_decay=args.weight_decay,
                artifact_path=artifact,
            )
        raise ValueError(f"unknown head {kind}")

    torch_version = prepare_torch()
    try:
        comparison = run_comparison(
            loaded.examples,
            cutoff=cutoff,
            min_label_support=args.min_label_support,
            max_terms=args.max_terms,
            min_df=args.min_df,
            trainer=trainer,
        )
    except InsufficientCorpus as exc:
        payload = _provenance(args.database_url, recorded_command, loaded)
        payload["status"] = "insufficient"
        payload["reason"] = str(exc)
        write_report(args.report, payload)
        print(str(exc), file=sys.stderr)
        return 2
    payload = _provenance(args.database_url, recorded_command, loaded)
    payload.update(comparison.to_dict())
    payload["text"] = {
        "fields": "title plus content prefix",
        "content_limit_chars": args.text_limit,
        "tokenizer": "lowercase tokens of three or more letters or digits, no synonym expansion",
        "naive_bayes_tokenizer": "the retrieval prior's synonym-expanded token set",
    }
    payload["training"] = {
        "epochs": args.epochs,
        "hidden": args.hidden,
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "seed": args.seed,
        "loss": "BCEWithLogitsLoss",
        "pos_weight_cap": 20,
        "mlp": "linear, relu, dropout 0.1, linear",
    }
    promoted = args.artifact_dir / "cfr_mlp.promoted.safetensors"
    artifacts: dict[str, object] = {
        "tfidf_logreg_sha256": file_sha256(linear_path),
        "mlp_sha256": file_sha256(mlp_path),
        "promoted_path": promoted.as_posix(),
        "promoted": comparison.promotion.promoted,
    }
    if comparison.promotion.promoted:
        promoted.write_bytes(mlp_path.read_bytes())
        artifacts["promoted_sha256"] = file_sha256(promoted)
    elif promoted.is_file():
        promoted.unlink()
    payload["artifacts"] = artifacts
    payload["torch_version"] = torch_version
    write_report(args.report, payload)
    print(args.report)
    return 0


def _provenance(
    database_url: str,
    command: str,
    loaded: LoadedCorpus,
) -> dict[str, object]:
    return {
        "recorded_at": recorded_at(),
        "git_sha": git_sha(),
        "command": command,
        "database": public_database_url(database_url),
        "hardware": host_summary(),
        "dataset_versions": dict(loaded.dataset_versions),
        "documents_loaded": len(loaded.examples),
        "skipped_without_date": loaded.skipped_without_date,
        "skipped_without_labels": loaded.skipped_without_labels,
        "skipped_without_text": loaded.skipped_without_text,
    }


if __name__ == "__main__":
    raise SystemExit(main())
