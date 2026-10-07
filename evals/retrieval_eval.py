"""Compare retrieval modes on the versioned sets.

Keyword quality uses the production scorer after one load of eligible sections.
Its uncached latency is copied from the latency report when that file exists,
because repeating the full Python scan would only restate that measurement.
"""

import argparse
import json
import time
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.services.classifier import CfrPrior, fit_cfr_prior_from_policies
from app.services.embeddings import Embedder, embedding_model_id, sentence_transformer_embedder
from app.services.retrieval import RetrievalMode, RetrievedPolicy, retrieve_policies
from evals.host import git_sha, host_summary, recorded_at
from evals.keyword_baseline import KeywordBaseline
from evals.retrieval_latency import percentile
from evals.retrieval_metrics import mean, mrr_at, ndcg_at, recall_at, relevant

_SET_NAMES = ("document_level_v1", "section_level_v1", "curated_v1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval modes")
    parser.add_argument("--sets-dir", type=Path, default=Path("evals/retrieval"))
    parser.add_argument(
        "--modes",
        default="keyword,fulltext,vector,hybrid,hybrid_prior",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/reports/retrieval_eval.json"),
    )
    args = parser.parse_args(argv)
    selected = [item.strip() for item in str(args.modes).split(",") if item.strip()]
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    embedder, model_id = _embedder(settings.embedding_model, settings.embedding_revision, selected)
    try:
        with factory() as session:
            prior = (
                fit_cfr_prior_from_policies(session) if "hybrid_prior" in selected else None
            )
            keyword = KeywordBaseline(session) if "keyword" in selected else None
            sets = _score_sets(
                session,
                args.sets_dir,
                selected,
                args.limit,
                embedder,
                model_id,
                prior,
                keyword,
            )
    finally:
        engine.dispose()
    payload: dict[str, object] = {
        "recorded_at": recorded_at(),
        "git_sha": git_sha(),
        "command": (
            "py -3 -m evals.retrieval_eval "
            f"--modes {args.modes} --limit {args.limit} --output {args.output.as_posix()}"
        ),
        "database": _database(settings.database_url),
        "hardware": host_summary(),
        "dataset_version": _dataset_version(),
        "embedding_model_id": model_id,
        "keyword_note": (
            "Keyword ranks use the production scorer after loading eligible sections once. "
            "Uncached keyword latency is the figure in "
            "evals/reports/retrieval_latency_2026-10-07.json."
        ),
        "lexical_overlap_note": (
            "lexical_overlap is the share of query tokens that also appear in the gold text. "
            "A high value on the document set means title words favor keyword search."
        ),
        "sets": sets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    markdown = args.output.with_suffix(".md")
    markdown.write_text(_markdown(payload), encoding="utf-8")
    print(args.output)
    return 0


def _score_sets(
    session: Session,
    sets_dir: Path,
    modes: list[str],
    limit: int,
    embedder: Embedder | None,
    model_id: str | None,
    prior: CfrPrior | None,
    keyword: KeywordBaseline | None,
) -> dict[str, object]:
    scored: dict[str, object] = {}
    for name in _SET_NAMES:
        path = sets_dir / f"{name}.jsonl"
        if not path.is_file():
            scored[name] = {"status": "missing", "path": path.as_posix()}
            continue
        items = _read_jsonl(path)
        overlaps: list[float] = []
        for item in items:
            overlap = item.get("lexical_overlap")
            if isinstance(overlap, int | float):
                overlaps.append(float(overlap))
        mode_scores: dict[str, object] = {}
        for mode in modes:
            mode_scores[mode] = _score_mode(
                session, items, mode, limit, embedder, model_id, prior, keyword
            )
        scored[name] = {
            "status": "scored",
            "items": len(items),
            "mean_lexical_overlap": mean(overlaps),
            "modes": mode_scores,
        }
    return scored


def _score_mode(
    session: Session,
    items: list[dict[str, object]],
    mode: str,
    limit: int,
    embedder: Embedder | None,
    model_id: str | None,
    prior: CfrPrior | None,
    keyword: KeywordBaseline | None,
) -> dict[str, object]:
    recalls: dict[int, list[float]] = {1: [], 5: [], 10: []}
    mrr: list[float] = []
    ndcg: list[float] = []
    elapsed: list[float] = []
    loaded_ms = None
    if mode == "keyword" and keyword is not None:
        before = keyword.load_ms
        keyword.warmup({_as_of(item.get("as_of")) for item in items})
        loaded_ms = keyword.load_ms - before
    excluded_ok = 0
    excluded_total = 0
    negative_with_hits = 0
    negative_total = 0
    for item in items:
        found, duration = _retrieve(session, item, mode, limit, embedder, model_id, prior, keyword)
        elapsed.append(duration)
        gold_sections = _strings(item.get("section_ids"))
        gold_documents = _strings(item.get("document_numbers"))
        forbidden = _strings(item.get("forbid_document_numbers"))
        if gold_sections or gold_documents:
            hits = [
                relevant(hit.section_id, hit.document_number, gold_sections, gold_documents)
                for hit in found
            ]
            gold_count = len(gold_sections) if gold_sections else len(gold_documents)
            for k in recalls:
                recalls[k].append(recall_at(hits, k))
            mrr.append(mrr_at(hits, 10))
            ndcg.append(ndcg_at(hits, gold_count, 10))
            continue
        if forbidden:
            excluded_total += 1
            returned = {hit.document_number for hit in found if hit.document_number}
            excluded_ok += int(returned.isdisjoint(forbidden))
            continue
        negative_total += 1
        negative_with_hits += int(bool(found))
    return {
        "queries": len(items),
        "ranked_queries": len(mrr),
        "recall_at_1": mean(recalls[1]),
        "recall_at_5": mean(recalls[5]),
        "recall_at_10": mean(recalls[10]),
        "mrr_at_10": mean(mrr),
        "ndcg_at_10": mean(ndcg),
        "p50_ms": percentile(elapsed, 0.50),
        "p95_ms": percentile(elapsed, 0.95),
        "exclusion_checks": excluded_total,
        "exclusion_held": excluded_ok,
        "negative_queries": negative_total,
        "negative_with_hits": negative_with_hits,
        "section_load_ms": loaded_ms,
    }


def _retrieve(
    session: Session,
    item: dict[str, object],
    mode: str,
    limit: int,
    embedder: Embedder | None,
    model_id: str | None,
    prior: CfrPrior | None,
    keyword: KeywordBaseline | None,
) -> tuple[list[RetrievedPolicy], float]:
    query = item.get("query")
    if not isinstance(query, str) or not query.strip():
        raise SystemExit(f"{item.get('id')} has no query")
    as_of = _as_of(item.get("as_of"))
    started = time.perf_counter()
    if mode == "keyword":
        if keyword is None:
            raise SystemExit("keyword mode needs the baseline cache")
        found = keyword.search(query, as_of, limit, {})
    else:
        selected: RetrievalMode = "hybrid" if mode == "hybrid_prior" else _mode(mode)
        found = retrieve_policies(
            session,
            query,
            as_of,
            limit,
            mode=selected,
            embedder=embedder,
            embedding_model_id=model_id,
            cfr_prior=prior if mode == "hybrid_prior" else None,
        )
    return found, (time.perf_counter() - started) * 1000


def _embedder(
    model_name: str,
    revision: str,
    modes: list[str],
) -> tuple[Embedder | None, str | None]:
    if not any(mode in {"vector", "hybrid", "hybrid_prior"} for mode in modes):
        return None, None
    embedder = sentence_transformer_embedder(model_name, revision)
    return embedder, embedding_model_id(model_name, revision)


def _mode(value: str) -> RetrievalMode:
    modes: dict[str, RetrievalMode] = {
        "keyword": "keyword",
        "fulltext": "fulltext",
        "vector": "vector",
        "embedding": "embedding",
        "hybrid": "hybrid",
    }
    selected = modes.get(value)
    if selected is None:
        raise SystemExit(f"unknown retrieval mode {value}")
    return selected


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        loaded = json.loads(line)
        if not isinstance(loaded, dict):
            raise SystemExit(f"{path} contains a non-object line")
        items.append({str(key): value for key, value in loaded.items()})
    return items


def _strings(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _as_of(value: object) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    return date(2026, 10, 7)


def _dataset_version() -> str | None:
    path = Path("evals/reports/federal_register_corpus_postgres.json")
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    version = loaded.get("dataset_version") if isinstance(loaded, dict) else None
    return version if isinstance(version, str) else None


def _database(url: str) -> str:
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://{rest.split('@', 1)[1]}"


def _markdown(payload: dict[str, object]) -> str:
    recorded = payload.get("recorded_at")
    lines = [
        "# Retrieval evaluation",
        "",
        f"Recorded {recorded}. Numbers come from the JSON report written beside this file.",
        "",
        "| set | mode | items | Recall@1 | Recall@5 | Recall@10 | MRR@10 | "
        "nDCG@10 | p50 ms | p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    sets = payload.get("sets")
    if isinstance(sets, dict):
        for name, body in sets.items():
            if not isinstance(body, dict):
                continue
            modes = body.get("modes")
            if not isinstance(modes, dict):
                lines.append(f"| {name} |  |  | missing |  |  |  |  |  |  |")
                continue
            for mode, scores in modes.items():
                if not isinstance(scores, dict):
                    continue
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(name),
                            str(mode),
                            _cell(scores.get("ranked_queries")),
                            _cell(scores.get("recall_at_1")),
                            _cell(scores.get("recall_at_5")),
                            _cell(scores.get("recall_at_10")),
                            _cell(scores.get("mrr_at_10")),
                            _cell(scores.get("ndcg_at_10")),
                            _cell(scores.get("p50_ms")),
                            _cell(scores.get("p95_ms")),
                        ]
                    )
                    + " |"
                )
    lines.append("")
    return "\n".join(lines)


def _cell(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return ""
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
