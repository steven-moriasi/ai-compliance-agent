# Roadmap

The repository is a reference implementation, not a production compliance product. Roadmap items
are ordered by risk reduction rather than feature volume.

## Near term: evaluation and evidence

- Build a versioned adversarial evaluation corpus covering unsupported citations, irrelevant
  retrieval, injection attempts, malformed provider output, and ambiguous policy text.
- Add measurable retrieval and analysis evaluation criteria without representing model confidence
  as ground truth.
- Exercise OIDC validation against a disposable JWKS server, including rotation, expiry, issuer,
  audience, and role-claim failures.
- Add PostgreSQL concurrency tests for competing workers, lease expiry, concurrent review, and
  notification reclaim.
- Add integration tests that prove review and outbox atomicity during injected failures.
- Define a prompt promotion process with evaluation results and rollback criteria.

## Next: operational maturity

- Use database time and renewable leases for long-running model calls.
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
