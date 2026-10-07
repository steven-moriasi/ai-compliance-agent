"""Embed policy sections into PostgreSQL.

The run is resumable. A section is rewritten only when its content hash changed
or the row is missing. Each batch commits, so a stopped run can continue.
"""

import argparse
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.services.embeddings import (
    embedding_model_id,
    section_embedding_key,
    section_embedding_text,
    sentence_transformer_embedder,
)
from app.services.fulltext import qualified_table
from app.services.vectors import vector_literal
from evals.host import git_sha, host_summary, recorded_at


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Embed policy sections")
    parser.add_argument("--model", default=settings.embedding_model)
    parser.add_argument("--revision", default=settings.embedding_revision)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(f"evals/reports/embedding_index_{date.today().isoformat()}.json"),
    )
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    model_id = embedding_model_id(args.model, args.revision)
    embedder = sentence_transformer_embedder(args.model, args.revision)
    engine = create_engine(settings.database_url)
    started = time.perf_counter()
    embedded = 0
    skipped = 0
    truncated = 0
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            embedded, skipped, truncated = _embed(
                session,
                embedder,
                model_id=model_id,
                batch_size=args.batch_size,
                only_missing=args.only_missing,
            )
            stored = _count(session, model_id)
    finally:
        engine.dispose()
    elapsed = time.perf_counter() - started
    payload = {
        "recorded_at": recorded_at(),
        "git_sha": git_sha(),
        "command": _command(args),
        "hardware": host_summary(),
        "model": args.model,
        "revision": args.revision,
        "model_id": model_id,
        "dimension": 384,
        "sections_embedded": embedded,
        "sections_skipped": skipped,
        "sections_stored": stored,
        "truncated_sections": truncated,
        "max_seq_length": int(getattr(embedder, "max_seq_length", 0)),
        "elapsed_seconds": elapsed,
        "sections_per_second": (embedded / elapsed) if elapsed and embedded else 0.0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.report)
    return 0


def _embed(
    session: Any,
    embedder: Any,
    *,
    model_id: str,
    batch_size: int,
    only_missing: bool,
) -> tuple[int, int, int]:
    sections = qualified_table(session, "policy_sections")
    embeddings = qualified_table(session, "section_embeddings")
    select_sql = text(
        f"""
        SELECT s.id, s.heading, s.text, e.content_sha256
        FROM {sections} AS s
        LEFT JOIN {embeddings} AS e
          ON e.section_id = s.id AND e.model_id = :model_id
        WHERE s.id > :last_id
        ORDER BY s.id
        LIMIT :batch_size
        """  # noqa: S608
    )
    insert_sql = text(
        f"""
        INSERT INTO {embeddings}
            (section_id, model_id, content_sha256, embedding)
        VALUES
            (:section_id, :model_id, :content_sha256, CAST(:embedding AS vector))
        ON CONFLICT (section_id, model_id) DO UPDATE
        SET content_sha256 = EXCLUDED.content_sha256,
            embedding = EXCLUDED.embedding,
            created_at = now()
        """  # noqa: S608
    )
    embedded = 0
    skipped = 0
    truncated = 0
    last_id = ""
    while True:
        rows = list(
            session.execute(
                select_sql,
                {"model_id": model_id, "last_id": last_id, "batch_size": batch_size},
            ).mappings()
        )
        if not rows:
            break
        last_id = str(rows[-1]["id"])
        pending: list[dict[str, str]] = []
        texts: list[str] = []
        page_texts: list[str] = []
        for row in rows:
            body = section_embedding_text(row["heading"], row["text"])
            page_texts.append(body)
            digest = section_embedding_key(row["heading"], row["text"])
            if row["content_sha256"] == digest or (
                only_missing and row["content_sha256"] is not None
            ):
                skipped += 1
                continue
            texts.append(body)
            pending.append({"section_id": str(row["id"]), "content_sha256": digest})
        truncated += int(embedder.truncated(page_texts))
        if not texts:
            continue
        vectors = embedder.embed(texts)
        session.execute(
            insert_sql,
            [
                {
                    "section_id": item["section_id"],
                    "model_id": model_id,
                    "content_sha256": item["content_sha256"],
                    "embedding": vector_literal(vector),
                }
                for item, vector in zip(pending, vectors, strict=True)
            ],
        )
        session.commit()
        embedded += len(pending)
        print(f"embedded {embedded} skipped {skipped}", flush=True)
    return embedded, skipped, truncated


def _count(session: Any, model_id: str) -> int:
    table = qualified_table(session, "section_embeddings")
    statement = text(f"SELECT count(*) FROM {table} WHERE model_id = :model_id")  # noqa: S608
    value = session.execute(statement, {"model_id": model_id}).scalar()
    return int(value or 0)


def _command(args: argparse.Namespace) -> str:
    parts = [
        "py -3 -m app.ingestion.embed",
        f"--model {args.model}",
        f"--revision {args.revision}",
        f"--batch-size {args.batch_size}",
    ]
    if args.only_missing:
        parts.append("--only-missing")
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
