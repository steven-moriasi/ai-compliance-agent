import argparse
import subprocess
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.ingestion.federal_register.client import FederalRegisterClient
from app.ingestion.federal_register.load import execute_ingest


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.since > args.until:
        parser.error("--since must be on or before --until")
    settings = get_settings()
    cache_dir = args.cache_dir or Path(settings.ingest_cache_dir)
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with FederalRegisterClient(cache_dir=cache_dir, contact=settings.ingest_contact) as client:
            with factory() as session:
                return execute_ingest(
                    session,
                    client,
                    agency=args.agency,
                    document_type=args.document_type,
                    cfr_title=args.cfr_title,
                    since=args.since,
                    until=args.until,
                    max_documents=args.max_docs,
                    incremental=args.incremental,
                    data_dir=args.data_dir,
                    git_sha=_git_sha(),
                )
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Federal Register documents")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--agency", default="environmental-protection-agency")
    ingest.add_argument("--type", default="RULE", dest="document_type")
    ingest.add_argument("--cfr-title", type=int, default=40)
    ingest.add_argument("--since", type=date.fromisoformat, default=date(2023, 1, 1))
    ingest.add_argument("--until", type=date.fromisoformat, default=date(2025, 12, 31))
    ingest.add_argument("--max-docs", type=int, default=600)
    ingest.add_argument("--incremental", action="store_true")
    ingest.add_argument("--cache-dir", type=Path)
    ingest.add_argument("--data-dir", type=Path, default=Path("data"))
    return parser


def _git_sha() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


if __name__ == "__main__":
    raise SystemExit(main())
