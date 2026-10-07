# ADR 008: Default PostgreSQL retrieval to hybrid

- Status: Accepted
- Date: 2026-10-07

## Context

Full text was the PostgreSQL default until the modes could be compared on one corpus.
`evals/reports/retrieval_eval_2026-10-07.json` scores keyword, full text, vector, hybrid, and
hybrid plus the CFR-part prior. Dataset `840a6be0a156`. Embedding model
`sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.

The document set has 199 items and mean lexical overlap 0.897. Title words favor
any retriever that can match those words. The curated set has 20 items, of which 14 have gold
sections. Two date checks must hide a document that is not yet effective. Four queries should
not be treated as ranked gold: three are out of scope, and `§ 63.7500` is not in this corpus.

Recall@5 on the document set:

| Mode | Recall@5 | MRR@10 |
| --- | --- | --- |
| keyword | 0.704 | 0.492 |
| fulltext | 0.206 | 0.149 |
| vector | 0.729 | 0.562 |
| hybrid | 0.729 | 0.549 |
| hybrid_prior | 0.729 | 0.551 |

Recall@5 on the 14 curated items with gold:

| Mode | Recall@5 | MRR@10 | Negative queries that returned hits |
| --- | --- | --- | --- |
| keyword | 0.071 | 0.071 | 4 |
| fulltext | 0.429 | 0.316 | 0 |
| vector | 0.5 | 0.301 | 4 |
| hybrid | 0.643 | 0.528 | 4 |
| hybrid_prior | 0.643 | 0.530 | 4 |

Every mode kept both not-yet-effective documents out of the results. The prior moves MRR@10 by
less than 0.003 on either set. Keyword quality on the document set comes with a one-time section
load of 228749.9 ms and is not a request path we can serve.

`section_level_v1` was not scored. That file was not built. Questions for it have to be
paraphrased by the local model.

## Decision

PostgreSQL defaults to hybrid. Callers can still select `fulltext`, `vector`, or `keyword`.

The CFR-part prior stays off the default path. It does not earn the extra score on this split,
and it was fit on every stored policy, including the documents being retrieved.

Full text remains the fallback when the embedding model or the vector index is unavailable.
It is also the only measured mode that returned no hits for the four negative curated queries.
Hybrid and vector both returned hits for all four. That is a weakness of the default, not a
reason to keep the lower curated Recall@5 of full text.

## Consequences

- A deployment without the retrieval extra, or without a built `section_embeddings` index, falls
  back to full text for analysis and records that fallback.
- The prior can be turned on later only with a new comparison on the same sets.
- Section-level questions are still an open measurement. See `docs/ROADMAP.md`.
