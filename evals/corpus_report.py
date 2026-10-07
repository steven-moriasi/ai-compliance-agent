"""Write a corpus report from the database that was just loaded.

The counts come from that database and from the quality file the ingester
already wrote. Elapsed time is passed in from the process that ran the load.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.enums import IngestionStatus, PolicyStatus
from app.domain.models import IngestionRun, Policy, PolicySection
from app.domain.types import JsonObject
from evals.host import git_sha, host_summary, recorded_at


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Federal Register corpus report")
    parser.add_argument("--elapsed-ms", type=int, required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/reports/federal_register_corpus_postgres.json"),
    )
    parser.add_argument("--quality-dir", type=Path, default=Path("data/quality"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/raw/federal_register"))
    args = parser.parse_args()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        with sessionmaker(bind=engine)() as session:
            payload = _payload(
                session,
                elapsed_ms=args.elapsed_ms,
                command=args.command,
                quality_dir=args.quality_dir,
                cache_dir=args.cache_dir,
                database=settings.database_url,
            )
    finally:
        engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


def _payload(
    session: Session,
    *,
    elapsed_ms: int,
    command: str,
    quality_dir: Path,
    cache_dir: Path,
    database: str,
) -> dict[str, object]:
    run = session.scalar(
        select(IngestionRun)
        .where(IngestionRun.status == IngestionStatus.SUCCEEDED)
        .order_by(IngestionRun.finished_at.desc())
    )
    if run is None:
        raise SystemExit("no succeeded ingestion run")
    quality = _quality(quality_dir / f"{run.run_id}.json")
    status_rows = session.execute(
        select(Policy.status, func.count()).group_by(Policy.status)
    ).all()
    status_counts = {
        getattr(status, "name", str(status)): int(count) for status, count in status_rows
    }
    publication = session.execute(
        select(func.min(Policy.publication_date), func.max(Policy.publication_date))
    ).one()
    cache_bytes, cache_files = _cache(cache_dir)
    return {
        "recorded_at": recorded_at(),
        "git_sha": git_sha(),
        "command": command,
        "command_elapsed_ms": elapsed_ms,
        "database": _redact_database(database),
        "hardware": host_summary(),
        "dataset_version": run.dataset_version,
        "run_id": run.run_id,
        "query": run.params,
        "documents_fetched": quality.get("documents", run.counts.get("documents")),
        "documents_inserted": run.counts.get("inserted"),
        "documents_unchanged": run.counts.get("unchanged"),
        "documents_without_sections": quality.get("documents_without_sections"),
        "parse_failures": run.counts.get("parse_failures"),
        "sections": session.scalar(select(func.count()).select_from(PolicySection)),
        "policies": session.scalar(select(func.count()).select_from(Policy)),
        "empty_sections_dropped": quality.get("empty_sections_dropped"),
        "sections_split": quality.get("sections_split"),
        "residual_markup": quality.get("residual_markup"),
        "publication_date_min": _iso(publication[0]),
        "publication_date_max": _iso(publication[1]),
        "policy_status_counts": status_counts,
        "active_policies": session.scalar(
            select(func.count()).select_from(Policy).where(Policy.status == PolicyStatus.ACTIVE)
        ),
        "cache_bytes": cache_bytes,
        "cache_files": cache_files,
    }


def _quality(path: Path) -> JsonObject:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _cache(directory: Path) -> tuple[int, int]:
    if not directory.is_dir():
        return 0, 0
    sizes = [path.stat().st_size for path in directory.rglob("*") if path.is_file()]
    return sum(sizes), len(sizes)


def _iso(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _redact_database(url: str) -> str:
    """Keep the dialect and database name. Drop credentials if the URL has them."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://{rest}"


if __name__ == "__main__":
    raise SystemExit(main())
