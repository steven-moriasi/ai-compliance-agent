# Roadmap

The repository is a reference implementation, not a production compliance product. Roadmap items
are ordered by risk reduction rather than feature volume.

## Current production gaps

- Policy sections are manually entered; there is no authenticated publication ingest, source
  signature, immutable source artifact, provenance chain, or section-to-source consistency check.
- The case date is derived from creation time. There is no explicit applicability date,
  jurisdiction, regulated-entity profile, or transitional-rule model.
- Effective windows are filtered but overlapping active versions and conflicting sections are not
  rejected automatically.
- Hybrid retrieval is measured on a document set and a curated set. The section-level question
  set is not built yet. Exact-quote checks still do not prove regulatory correctness.
- A PyTorch CFR-part MLP was scored against majority, naive Bayes, and TF-IDF logistic
  regression on a 2024-07-01 split. The promotion gate refused it. The score does not approve
  or reject a case. See [ADR 009](adr/009-cfr-classifier-not-promoted.md).
- The deterministic corpus proves application behavior against synthetic fixtures; it is not a
  legal-expert benchmark and does not measure live-provider quality.
- PostgreSQL concurrency tests cover claiming, fencing, and notification reclaim, but not sustained
  throughput, failover, or multi-region clock behavior.
- Human review has an API boundary but no assignment, escalation, workload, or separation-of-duties
  workflow.

## Near term: evaluation and evidence

- Add measurable retrieval recall and analysis criteria using reviewed source fixtures without
  representing model confidence as ground truth.
- Add conflicting-version, ambiguous-section, jurisdiction, and applicability-date cases to the
  versioned corpus.
- Exercise OIDC validation against a disposable JWKS server, including rotation, expiry, issuer,
  audience, and role-claim failures.
- Add PostgreSQL tests for concurrent review and review/outbox atomicity during injected failures.
- Define a prompt promotion process with evaluation results and rollback criteria.
- Define source-ingestion provenance, authenticity checks, and approval before a policy version
  becomes active.

## Next: operational maturity

- Lease renewal during retrieval and provider calls is implemented. Database time, instead of
  the application clock, is not.
- Add queue-age, review-age, dead-letter, token, and estimated-cost metrics.
- Add structured logging with correlation IDs and explicit sensitive-field redaction.
- Add controlled dead-letter re-drive with an operator audit event.
- Add graceful shutdown so workers stop claiming work and finish or relinquish active leases.
- Add backup-restore exercises and migration compatibility checks across releases.
- Add OpenTelemetry traces across API, worker, provider, review, and notification boundaries.

## Production adaptation

- Move source documents to encrypted object storage with malware scanning, retention, legal hold,
  and tenant-aware access controls.
- Add tenant boundaries, row-level authorization, reviewer assignment, and separation of duties.
- Integrate a managed secret store and workload identity.
- Add edge rate limits, request quotas, and provider budget enforcement.
- Export audit events to immutable storage with integrity verification.
- Add webhook timestamp/replay-window verification and signing-key rotation.
- Add software bill of materials, vulnerability scanning, image signing, and deployment
  attestations.
- Establish provider data-use, regional processing, and retention controls.

## Scale triggers

Retain the PostgreSQL queue until measurements show that polling, lock contention, connection use,
or fan-out are limiting objectives. At that point, evaluate a broker while retaining database
idempotency, domain state, and fencing. Do not introduce distributed infrastructure solely to make
the system appear complex.

## Explicit non-goals

- autonomous legal or regulatory decisions;
- claims of certification or regulatory compliance;
- training a foundation model;
- a conversational user interface;
- invented production traffic, accuracy, cost savings, or availability metrics.
