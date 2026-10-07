# ADR 007: Rank the corpus in PostgreSQL

- Status: Accepted
- Date: 2026-10-07

## Context

Keyword retrieval loads every eligible section into Python and scores token overlap there.
On the Federal Register corpus that took 124411 ms in SQLite
(`evals/reports/keyword_retrieval.json`) and a PostgreSQL p50 of 34808.43209999148 ms
(`evals/reports/retrieval_latency_2026-10-07.json`). The default analysis lease is 120 seconds.
An embedding file under `data/embeddings/` was never built, and cosine similarity in Python
would have had the same full-scan shape.

## Decision

Keep keyword overlap as the SQLite path and as the evaluation baseline. On PostgreSQL:

- store a generated `tsvector` on `policy_sections` and a GIN index;
- filter `ACTIVE` and the effective-date window in SQL;
- rank full text with `ts_rank_cd` and return a bounded candidate list;
- store 384-dimensional MiniLM vectors in `section_embeddings` with an HNSW cosine index;
- fuse full text and vectors with reciprocal rank fusion (k = 60) only after both SQL lists return.

If the embedding model or the vector table is missing, analysis falls back to full text, writes a
`retrieval_fallback` audit event, and increments `compliance_retrieval_fallback_total`. The case
is not dropped.

The embedded model is `sentence-transformers/all-MiniLM-L6-v2` at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, loaded from safetensors. The index build is
`evals/reports/embedding_index_2026-10-07.json`: 34174 sections stored, 24272 truncated at
`max_seq_length` 256, 6.054416633259385 sections per second.

## Consequences

- A full-text query no longer reads the corpus into the worker. Measured p95 on 50 titles was
  131.6251999232918 ms.
- Vector p95 on the same titles was 236.06449492508546 ms. Hybrid p95 was
  1594.2314949992574 ms (`evals/reports/retrieval_latency_vector_2026-10-07.json`).
- Most stored sections are longer than the embedding model's 256 tokens, because chunking allows
  about 350 words. Those vectors are truncated. That is a limit of this index, not a silent skip.
- Keyword search remains too slow to be the PostgreSQL default.
