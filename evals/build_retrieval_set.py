"""Write the document-level and curated retrieval sets.

Section-level questions are not built here. They have to be paraphrased by the
local model, and a template would copy the section text.
"""

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from evals.retrieval_sets import CURATED, load_curated_items, load_document_items, write_jsonl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build retrieval evaluation sets")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 7))
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/retrieval"))
    args = parser.parse_args(argv)
    settings = get_settings()
    cache_dir = args.cache_dir or Path(settings.ingest_cache_dir)
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            documents = load_document_items(
                session,
                cache_dir,
                count=args.count,
                seed=args.seed,
                as_of=args.as_of,
            )
            curated = load_curated_items(session, CURATED)
    finally:
        engine.dispose()
    write_jsonl(args.output_dir / "document_level_v1.jsonl", documents)
    write_jsonl(args.output_dir / "curated_v1.jsonl", curated)
    with_action = sum(1 for item in documents if item["source"] == "action+title")
    print(f"documents {len(documents)} with_action {with_action}")
    print(f"curated {len(curated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
