# Measured results and operational limits

Figures in this file are copied from `evals/reports/`. A number that is not in those files was not
measured. None of these figures is a legal-accuracy, recall, or availability claim.

## Host

Recorded in `evals/reports/hardware.json` at `2026-10-06T21:38:07Z`, git `46fc3bd88baa42660354797d21e73b4968ce7ef5`.

| Item | Measured value |
| --- | --- |
| CPU | Intel Core i5-1035G1 @ 1.00 GHz |
| Cores / logical processors | 4 / 8 |
| RAM | 15.6 GB |
| OS | Windows 11 Pro 10.0.26200 |
| Disk `D:` | 109.4 GB, 102.7 GB free |
| GPU | none (`nvidia-smi` was not installed) |
| Python | 3.14.7, AMD64 |

The torch 2.14.1 cp314 wheel was selected by a dry run and was not installed. The
sentence-transformers 6.1.0 resolution selected that torch wheel and was not installed. The report
states the machine is CPU only, so a later local model should stay at the small end of the 3B to 8B
range.

## Federal Register probe

Recorded in `evals/reports/federal_register_probe.json` at the same time as the hardware report.

The query was EPA, type `RULE`, CFR title 40, publication dates `2023-01-01` through `2025-12-31`.
The API reported `count` 995, 50 pages, and 20 results on the first page. `full_text_xml_url` and
`raw_text_url` returned HTTP 200 `text/html`, 10596 bytes, titled "Federal Register :: Request
Access". A browser user agent did not change that page. GovInfo issue XML returned
`application/xml`: `FR-2025-12-19.xml` was 3394987 bytes and `FR-2024-09-03.xml` was 5579395 bytes.

## Twenty-document load

Recorded in `evals/reports/federal_register_sample.json` at `2026-10-06T22:55:42Z`, git
`c164656aa42bdc74c0719ea1130eb0be5f3d1fee`. Elapsed time was 95859 ms, including an Alembic upgrade
of an empty SQLite database.

| Item | Measured value |
| --- | --- |
| Dataset | `d41c1d903433` |
| Documents loaded | 20 |
| Parse failures | 0 |
| Sections | 2177 |
| Short sections dropped | 61 |
| Sections split | 379 |
| Publication dates | `2023-01-04` to `2023-01-30` |
| Policy status | 20 `ACTIVE` |
| Cache | 44811232 bytes, 31 XML files |

## Corpus load

Recorded in `evals/reports/federal_register_corpus.json` at `2026-10-06T23:34:15Z`, same git SHA as
the sample. Elapsed time was 39249 ms. The note says this was a second load and the issue XML was
already cached.

| Item | Measured value |
| --- | --- |
| Dataset | `7a9a5318ad2f` |
| Query window | `2023-01-01` to `2025-12-31`, max 600, oldest first |
| Documents fetched | 600 |
| Documents loaded | 588 |
| Inserted / unchanged | 568 / 20 |
| Dropped for no remaining sections | 12 |
| Parse failures | 0 |
| Sections | 34174 |
| Short sections dropped | 1024 |
| Sections split | 5170 |
| Residual markup | 0 |
| Duplicate section references | 0 |
| Publication dates | `2023-01-04` to `2024-10-11` |
| Policy status | 588 `ACTIVE` |
| Cache | 1213814149 bytes, 907 XML files |

The query asked for publications through `2025-12-31`. The loaded documents end on `2024-10-11`.

## PostgreSQL corpus load

Recorded in `evals/reports/federal_register_corpus_postgres.json` at `2026-10-07T10:04:37Z`, git
`c892e10c4b107d2dc0bd3e8af858f4f29ce5c096`. Elapsed time was 130502 ms. The cache was already on
disk. The database URL in the report is `postgresql+psycopg://127.0.0.1:5432/compliance`.

| Item | Measured value |
| --- | --- |
| Dataset | `840a6be0a156` |
| Documents fetched | 600 |
| Inserted / unchanged | 588 / 0 |
| Dropped for no remaining sections | 12 |
| Parse failures | 0 |
| Policies | 588 |
| Sections | 34174 |
| Short sections dropped | 1024 |
| Sections split | 5170 |
| Residual markup | 0 |
| Publication dates | `2023-01-04` to `2024-10-11` |
| Policy status | 588 `ACTIVE` |
| Cache | 1213814149 bytes, 908 files |

The manifest hash includes the git SHA, so this reload is not dataset `7a9a5318ad2f`. Counts and
publication dates match that earlier SQLite load.

## Keyword retrieval

Recorded in `evals/reports/keyword_retrieval.json` at `2026-10-07T08:52:48Z`, git
`89d559fb2dbc3b5a2fd2cecef4a81e760ca9d6e6`, database `sqlite:///./compliance.db`.

One call, `retrieve_policies` for `nitrogen oxides`, keyword mode, as of `2026-10-07`, limit 5:

| Item | Measured value |
| --- | --- |
| Active policies in that database | 588 |
| Sections in that database | 34174 |
| Elapsed time | 124411 ms |
| Hits | 5 |
| First section | `preamble:summary` |
| First lexical score | 1.0 |

This is one SQLite observation. It is not a percentile. The PostgreSQL timings below repeat keyword
search on the same corpus shape.

The default `analysis_lease_seconds` in application settings is 120. This query took 124.411
seconds, so a worker on that default can fail to store a result for this corpus. Raise
`COMPLIANCE_ANALYSIS_LEASE_SECONDS` from an observed duration. Do not treat 124411 ms as a
high percentile.

## PostgreSQL retrieval latency

Recorded in `evals/reports/retrieval_latency_2026-10-07.json` at `2026-10-07T10:39:49Z`, git
`c892e10c4b107d2dc0bd3e8af858f4f29ce5c096`, dataset `840a6be0a156`, as of `2026-10-07`. Queries are
50 active policy titles drawn with seed 7. Ingestion does not store the Federal Register action
line, so these are not action-plus-title questions. Keyword mode ran on the first 10 of those
titles because it still loads every eligible section into Python.

| Mode | Queries | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| fulltext | 50 | 4.9 ms | 131.6 ms | 193.3 ms |
| keyword | 10 | 34808.4 ms | 40368.2 ms | 41023.5 ms |

Full-text p95 is under one second on this host. Keyword p50 on PostgreSQL is 34808 ms, which
finishes inside the default 120 second lease. The SQLite observation above does not.

The same report records single full-text probes. `NOx` and `nitrogen oxides` returned the same
first section, `preamble:II.A.1`. `PM2.5` returned `§ 52.1770#5` first. `§ 60.4` returned
`preamble:III.F` first, at lexical score 0.3, not a section whose reference is `§ 60.4`. The
stored vector is the heading plus the section text. It does not include the section reference.

## Embedding index

Recorded in `evals/reports/embedding_index_2026-10-07.json` at `2026-10-07T12:37:07Z`, git
`aa222a341b9dd4171fa5a75d68865c7f0851a6bd`. Model
`sentence-transformers/all-MiniLM-L6-v2` revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`,
384 dimensions, safetensors.

| Item | Measured value |
| --- | --- |
| Sections stored | 34174 |
| Sections embedded in that process | 32830 |
| Sections skipped because they were already stored | 1344 |
| Truncated at `max_seq_length` 256 | 24272 |
| Elapsed seconds | 5422.5 |
| Sections per second | 6.1 |

The skipped rows are the batches committed before this process was resumed. Throughput is
embedded sections divided by this process's elapsed time, including model load. 24272 truncated
sections follow from chunking near 350 words into a 256-token model. Those vectors were still stored.

## Vector and hybrid latency

Recorded in `evals/reports/retrieval_latency_vector_2026-10-07.json` at `2026-10-07T12:41:29Z`,
same git SHA and dataset, same 50 titles and seed as the full-text latency report.

| Mode | Queries | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| vector | 50 | 164.9 ms | 236.1 ms | 267.7 ms |
| hybrid | 50 | 247.5 ms | 1594.2 ms | 1815.9 ms |

Hybrid p95 on these titles is over one second. Full-text p95 on the same titles is not.

## Retrieval quality

Recorded in `evals/reports/retrieval_eval_2026-10-07.json` at `2026-10-07T13:06:56Z`. The document
set has 199 items and mean lexical overlap 0.897. The curated set has 20 items and
mean lexical overlap 0.583. `section_level_v1` is missing. Recall@5:

| Mode | Document set | Curated set (14 ranked items) |
| --- | --- | --- |
| keyword | 0.704 | 0.071 |
| fulltext | 0.206 | 0.429 |
| vector | 0.729 | 0.5 |
| hybrid | 0.729 | 0.643 |
| hybrid_prior | 0.729 | 0.643 |

Keyword `p50_ms` in that report is the score after the sections were loaded. The document-set
load was 228749.9 ms. It is not the uncached keyword latency in the table above.
Full text returned hits for 0 of 4 negative curated queries. Vector, hybrid, hybrid plus the
prior, and keyword returned hits for all 4. The prior does not move curated MRR@10 by more than
0.002. [ADR 008](adr/008-hybrid-retrieval-default.md) keeps it off the default path.

## CFR-part classifiers

Recorded in `evals/reports/cfr_classifier_2026-10-07.json` at `2026-10-07T15:03:19Z`, git
`fef5cea5bd6135ee49abcddb263065403c7ee054`, dataset `840a6be0a156`, torch `2.14.1+cpu`. Command:

`py -3 -m ml.cfr_classifier.train --cutoff 2024-07-01 --min-label-support 5 --text-limit 4000 --max-terms 2000 --min-df 3 --epochs 40 --hidden 64 --learning-rate 0.001 --weight-decay 0.0001 --seed 7 --artifact-dir data\models --report evals\reports\cfr_classifier_2026-10-07.json`

The database URL came from `COMPLIANCE_DATABASE_URL`. The report stores
`postgresql+psycopg://127.0.0.1:5432/compliance` with the userinfo removed. Text is the title
plus the first 4000 characters. Torch heads use raw tokens. Naive Bayes uses the synonym-expanded
token set. 588 documents were loaded. After dropping labels with fewer than five training
documents, 409 train and 99 test remained. 65 train and 15 test documents were dropped.

| Model | Top-1 hit rate | Recall@3 | Elapsed seconds |
| --- | --- | --- | --- |
| majority | 0.5252525252525253 | 0.7676767676767676 | 0.00013399997260421515 |
| naive Bayes | 0.8383838383838383 | 0.9494949494949495 | 1.6352010000264272 |
| TF-IDF logistic regression | 0.8484848484848485 | 0.9595959595959596 | 2.522956599947065 |
| MLP | 0.8383838383838383 | 0.9090909090909091 | 0.37268259993288666 |

Logistic regression ran before the MLP, so its elapsed time includes first-use CPU work after
the torch import. The MLP ties naive Bayes on top-1 and is lower on Recall@3. Logistic regression
is ahead of both. Gold population stability is 0.6884761992732462, above the 0.25 cutoff, so the
report marks the label mix as drifted. PCA L2 mean shift is 0.141372630378354. The promotion
gate refused the MLP. [ADR 009](adr/009-cfr-classifier-not-promoted.md) leaves it out of review
and out of default retrieval. Weights remain in gitignored `data/models/`.

## Not measured

- `evals/reports/local_model.json` does not exist. Generation latency, token counts, and output
  quality for `qwen2.5:3b` were not measured.
- No section-level question set was scored. Building it requires the local model to paraphrase
  sections without copying long spans.
- The disk-free figure above is the 2026-10-06 snapshot. It does not include later cache growth.
- Coverage percentages from local test runs are not in `evals/reports/`. The CI gate is 85%
  coverage. That gate does not measure retrieval quality or legal correctness.
