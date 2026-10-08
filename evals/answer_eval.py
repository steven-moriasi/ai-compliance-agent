"""Measure policy answers against a versioned question set.

Run it against the loaded corpus with the provider you want to measure, for example
`COMPLIANCE_MODEL_PROVIDER=ollama python -m evals.answer_eval`. Each question goes through
the same retrieval, prompt and quote checks as the worker. Questions are stored with
`requested_by = answer-eval` so the runs stay auditable.

In scope: answered, and at least one citation comes from an expected Federal Register document.
Not in force: no citation from a document published after the question's date.
Out of scope: not answered.
"""

import argparse
import json
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.domain.enums import QuestionStatus
from app.domain.models import PolicyQuestion
from app.domain.types import JsonObject
from app.services.answers import AnswerService
from app.services.embeddings import Embedder
from app.services.provider_factory import build_provider
from app.services.providers import AnswerProvider
from evals.host import git_sha, host_summary, recorded_at
from evals.retrieval_latency import percentile

EVAL_ACTOR = "answer-eval"


@dataclass(frozen=True)
class AnswerItem:
    id: str
    category: str
    question: str
    as_of: date
    expected_document_numbers: tuple[str, ...]
    forbidden_document_numbers: tuple[str, ...]


def load_items(path: Path) -> list[AnswerItem]:
    items: list[AnswerItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        items.append(
            AnswerItem(
                id=str(row["id"]),
                category=str(row["category"]),
                question=str(row["question"]),
                as_of=date.fromisoformat(str(row["as_of"])),
                expected_document_numbers=tuple(row.get("expected_document_numbers", [])),
                forbidden_document_numbers=tuple(row.get("forbidden_document_numbers", [])),
            )
        )
    return items


def evaluate(
    session: Session,
    items: Sequence[AnswerItem],
    settings: Settings,
    provider: AnswerProvider,
    embedder_loader: Callable[[], Embedder] | None = None,
) -> JsonObject:
    rows: list[JsonValue] = []
    for item in items:
        question = _claimed_question(session, item, settings)
        answered = AnswerService(
            session,
            settings,
            provider,
            embedder_loader=embedder_loader,
        ).answer(question, correlation_id=f"answer-eval-{item.id}", worker_id=EVAL_ACTOR)
        rows.append(_row(item, answered))
    return {"summary": _summary([row for row in rows if isinstance(row, dict)]), "items": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate grounded policy answers")
    parser.add_argument("--set", type=Path, default=Path("evals/answers/questions_v1.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("evals/reports/answer_eval.json"))
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = build_provider(settings)
    from app.worker import load_worker_embedder

    embedder_loader = load_worker_embedder(settings, engine.dialect.name)
    items = load_items(args.set)
    with factory() as session:
        result = evaluate(session, items, settings, provider, embedder_loader)
    report: JsonObject = {
        "recorded_at": recorded_at(),
        "git_sha": git_sha(),
        "host": host_summary(),
        "question_set": str(args.set),
        "provider": provider.name,
        "model": provider.model,
        "retrieval_mode_setting": settings.retrieval_mode,
        "source_limit": settings.question_source_limit,
        "source_chars": settings.question_source_chars,
        **result,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


def _claimed_question(session: Session, item: AnswerItem, settings: Settings) -> PolicyQuestion:
    """Store the question already claimed by this run, so no queued question is touched."""
    now = datetime.now(UTC)
    question = PolicyQuestion(
        id=str(uuid.uuid4()),
        question=item.question,
        as_of=item.as_of,
        requested_by=EVAL_ACTOR,
        status=QuestionStatus.ANSWERING,
        worker_id=EVAL_ACTOR,
        fencing_token=1,
        attempts=1,
        started_at=now,
        lease_expires_at=now + timedelta(seconds=settings.analysis_lease_seconds * 10),
    )
    session.add(question)
    session.commit()
    return question


def _row(item: AnswerItem, question: PolicyQuestion) -> JsonObject:
    documents = {
        int(str(source["source"])): source.get("document_number") for source in question.sources
    }
    cited = sorted(
        {
            str(documents[int(str(citation["source"]))])
            for citation in question.citations
            if documents.get(int(str(citation["source"])))
        }
    )
    source_documents: list[JsonValue] = []
    source_documents.extend(sorted({str(number) for number in documents.values() if number}))
    answered = question.status == QuestionStatus.ANSWERED
    if item.category == "in-scope":
        passed = answered and bool(set(cited) & set(item.expected_document_numbers))
    elif item.category == "not-in-force":
        passed = not set(cited) & set(item.forbidden_document_numbers)
    else:
        passed = not answered
    return {
        "id": item.id,
        "category": item.category,
        "as_of": item.as_of.isoformat(),
        "status": question.status.value,
        "passed": passed,
        "answer": question.answer,
        "cited_document_numbers": list(cited),
        "source_document_numbers": source_documents,
        "validation_errors": list(question.validation_errors),
        "retrieval_mode": question.retrieval_mode,
        "latency_ms": question.latency_ms,
        "input_tokens": question.input_tokens,
        "output_tokens": question.output_tokens,
    }


def _summary(rows: list[JsonObject]) -> JsonObject:
    summary: JsonObject = {}
    for category in ("in-scope", "not-in-force", "out-of-scope"):
        chosen = [row for row in rows if row["category"] == category]
        if not chosen:
            continue
        answered = sum(row["status"] == "answered" for row in chosen)
        passed = sum(bool(row["passed"]) for row in chosen)
        summary[category] = {
            "items": len(chosen),
            "answered": answered,
            "passed": passed,
            "pass_rate": round(passed / len(chosen), 3),
        }
    reasons = Counter(
        str(error).partition(":")[0]
        for row in rows
        if row["status"] != "answered"
        for error in _strings(row["validation_errors"])
    )
    latencies = [float(str(row["latency_ms"])) for row in rows if row["latency_ms"] is not None]
    p50 = percentile(latencies, 0.5)
    p95 = percentile(latencies, 0.95)
    summary["unanswered_reasons"] = dict(sorted(reasons.items()))
    summary["latency_ms"] = {
        "p50": round(p50, 1) if p50 is not None else None,
        "p95": round(p95, 1) if p95 is not None else None,
    }
    return summary


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
