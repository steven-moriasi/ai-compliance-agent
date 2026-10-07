"""Measure retrieval latency on the database named by COMPLIANCE_DATABASE_URL.

Keyword search still reads every eligible section in Python, so it runs on a
smaller query count when asked. The report says which count was used.
"""

import argparse
import json
import math
import random
import time
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.enums import PolicyStatus
from app.domain.models import Policy
from app.domain.types import JsonObject
from app.services.retrieval import RetrievalMode, retrieve_policies
from evals.host import git_sha, host_summary, recorded_at


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure retrieval latency")
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--keyword-queries", type=int, default=10)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 7))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/reports/retrieval_latency_2026-10-07.json"),
    )
    args = parser.parse_args()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            queries = _sample_titles(session, args.queries, args.seed)
            checks = _sanity(session, args.as_of, args.limit)
            modes: dict[str, JsonObject] = {
                "fulltext": _time_mode(session, queries, "fulltext", args.as_of, args.limit),
                "keyword": _time_mode(
                    session,
                    queries[: args.keyword_queries],
                    "keyword",
                    args.as_of,
                    args.limit,
                ),
            }
    finally:
        engine.dispose()
    payload: dict[str, object] = {
        "recorded_at": recorded_at(),
        "git_sha": git_sha(),
        "command": (
            "py -3 -m evals.retrieval_latency "
            f"--queries {args.queries} --keyword-queries {args.keyword_queries} "
            f"--limit {args.limit} --seed {args.seed} --as-of {args.as_of.isoformat()}"
        ),
        "database": _database(settings.database_url),
        "hardware": host_summary(),
        "dataset_version": _dataset_version(),
        "as_of": args.as_of.isoformat(),
        "seed": args.seed,
        "query_source": (
            "Active policy titles, sampled with a fixed seed. "
            "Ingestion does not store the Federal Register action line, "
            "so these are not yet the phase-3 action-plus-title questions."
        ),
        "keyword_note": (
            "Keyword retrieval still loads every eligible section into Python. "
            f"It was timed on the first {args.keyword_queries} sampled titles, not the full set."
        ),
        "modes": modes,
        "sanity": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


def _sample_titles(session: Session, count: int, seed: int) -> list[dict[str, str]]:
    rows = session.execute(
        select(Policy.document_number, Policy.title).where(
            Policy.status == PolicyStatus.ACTIVE,
            Policy.title.is_not(None),
        )
    ).all()
    pool = [
        {"document_number": str(number), "query": str(title)}
        for number, title in rows
        if number and title
    ]
    if len(pool) < count:
        raise SystemExit(f"need {count} titled policies, found {len(pool)}")
    return random.Random(seed).sample(pool, count)  # noqa: S311


def _time_mode(
    session: Session,
    queries: list[dict[str, str]],
    mode: RetrievalMode,
    as_of: date,
    limit: int,
) -> JsonObject:
    if queries:
        retrieve_policies(session, queries[0]["query"], as_of, limit, mode=mode)
    elapsed: list[float] = []
    hits: list[int] = []
    for item in queries:
        started = time.perf_counter()
        found = retrieve_policies(session, item["query"], as_of, limit, mode=mode)
        elapsed.append((time.perf_counter() - started) * 1000)
        hits.append(len(found))
    return {
        "queries": len(queries),
        "p50_ms": percentile(elapsed, 0.50),
        "p95_ms": percentile(elapsed, 0.95),
        "max_ms": max(elapsed) if elapsed else None,
        "min_ms": min(elapsed) if elapsed else None,
        "mean_hits": (sum(hits) / len(hits)) if hits else None,
    }


def _sanity(session: Session, as_of: date, limit: int) -> list[JsonObject]:
    probes = ("NOx", "nitrogen oxides", "PM2.5", "§ 60.4")
    recorded: list[JsonObject] = []
    for query in probes:
        started = time.perf_counter()
        found = retrieve_policies(session, query, as_of, limit, mode="fulltext")
        recorded.append(
            {
                "query": query,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "hits": [
                    {"section_ref": item.section_ref, "lexical_score": item.lexical_score}
                    for item in found
                ],
            }
        )
    return recorded


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


if __name__ == "__main__":
    raise SystemExit(main())
