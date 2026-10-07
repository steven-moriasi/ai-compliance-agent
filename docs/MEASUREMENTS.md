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

This is one SQLite observation. It is not a percentile and it was not repeated on PostgreSQL.

The default `analysis_lease_seconds` in application settings is 120. This query took 124.411
seconds, so a worker on that default can fail to store a result for this corpus. Raise
`COMPLIANCE_ANALYSIS_LEASE_SECONDS` from an observed duration. Do not treat 124411 ms as a
high percentile.

## Not measured

- `evals/reports/local_model.json` does not exist. Generation latency, token counts, and output
  quality for `qwen2.5:3b` were not measured.
- The retrieval extra was not installed, and no embedding-index report exists. Embedding and hybrid
  latency were not measured. Keyword mode remains the default.
- No CFR-part drift report was written. The 0.25 population-stability cutoff in code is a check
  threshold, not a measured shift on this corpus.
- The disk-free figure above is the 2026-10-06 snapshot. It does not include later cache growth.
- Coverage percentages from local test runs are not in `evals/reports/`. The CI gate is 85%
  coverage. That gate does not measure retrieval quality or legal correctness.
