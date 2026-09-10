# Threat model

## Method and scope

This document applies a STRIDE-oriented review to the API, analysis worker, PostgreSQL database,
model-provider integration, and notification webhook. It covers the reference implementation as
shipped, not controls supplied by a future cloud platform.

## Assets

- source documents that may contain confidential or personal data;
- policy text and policy-version provenance;
- prompt templates and output-schema versions;
- model results, confidence values, citations, and validation findings;
- reviewer identity, rationale, and final decisions;
- audit events and analysis hashes;
- OIDC, model-provider, database, and webhook credentials.

## Threat actors

- an unauthenticated external caller;
- an authenticated user exceeding their assigned role;
- a submitter placing adversarial instructions in a document;
- a compromised or faulty model provider;
- a stale worker attempting a late write;
- a network observer or spoofed webhook consumer;
- an operator with database or deployment access.

## Trust assumptions

- TLS is terminated by the deployment platform for all external connections.
- The identity provider protects signing keys and emits the configured issuer and audience.
- PostgreSQL authentication, encryption, backup, and operator access are platform responsibilities.
- Secrets are injected from a secret manager and are not committed or placed in command arguments.
- The webhook consumer verifies signatures, timestamps delivery independently, and deduplicates by
  `Idempotency-Key`.

## STRIDE analysis

| Threat | Example | Implemented control | Residual risk / production control |
| --- | --- | --- | --- |
| Spoofing | forged API identity | RS256 signature, issuer, audience, and JWKS validation | enforce TLS, key rotation, short token lifetime, and IdP conditional access |
| Spoofing | forged notification | HMAC-SHA256 over canonical body | rotate signing keys and use constant-time verification at consumer |
| Tampering | document changed after analysis | content hash stored at ingestion | protect database and verify hash when exporting evidence |
| Tampering | model cites unrelated policy | citation must reference retrieved policy/version and exact quote | retrieval quality can omit relevant policies; evaluate recall |
| Tampering | stale worker overwrites recovery | lease, worker identity, and monotonically increasing fencing token | database time should replace application time across regions |
| Repudiation | reviewer denies decision | actor ID, rationale, analysis hash, timestamp, and audit event | use append-only/WORM export and signed audit batches |
| Information disclosure | provider exception leaks secrets | stable public error code and redacted message | add centralized log redaction and provider data-retention agreement |
| Information disclosure | oversized document exhausts resources | configured byte limit before persistence | add gateway body limit, malware scanning, classification, and retention |
| Denial of service | duplicate case flood | idempotency key plus database uniqueness | add per-identity rate and quota enforcement |
| Denial of service | worker repeatedly crashes | bounded leases, recovery, and attempt limit | alert on queue age, failure rate, and exhausted attempts |
| Elevation of privilege | analyst performs review | endpoint-specific role dependencies | map roles from a controlled claim and test IdP group mappings |
| Elevation of privilege | development headers used in production | development auth rejected outside development | fail deployment policy if production uses development auth mode |

## Prompt-injection defense

Document text is always considered data, not instruction. The provider request:

1. places system instructions in the system role;
2. serializes the document under an `untrusted_document` field;
3. supplies a bounded retrieved policy context;
4. requests a strict JSON schema;
5. validates every citation against the retrieved policy version and exact source text;
6. records known injection signals as validation findings;
7. sends every completed result to a human reviewer.

These controls reduce risk but do not prove semantic correctness. A model result cannot approve,
reject, notify, or mutate policies without deterministic application logic and human action.

## Authorization matrix

| Capability | Admin | Analyst | Reviewer | Viewer |
| --- | ---: | ---: | ---: | ---: |
| Create policies and prompts | yes | no | no | no |
| Ingest documents | yes | yes | no | no |
| Create analysis cases | yes | yes | no | no |
| Read cases, catalogs, and audit | yes | yes | yes | yes |
| Approve or reject cases | yes | no | yes | no |

Production identity configuration must use an allowlisted claim contract. Arbitrary user-controlled
headers are only accepted when both authentication mode and environment are explicitly
`development`.

## Secret handling

Required secrets are:

- model-provider API key when a remote provider is enabled;
- PostgreSQL credentials;
- OIDC deployment/client configuration where private values are required;
- notification webhook signing secret.

Use a managed secret store, workload identity where supported, and independent credentials per
environment. Rotate model and webhook credentials without rebuilding the image. Never log
authorization headers, complete provider responses, signing secrets, or database URLs containing
passwords.

## Data protection and privacy

A production owner must define:

- data classification and whether documents may be sent to a third-party model;
- regional processing and data-residency requirements;
- retention periods for source documents, analyses, audits, and notifications;
- deletion and legal-hold procedures;
- encryption-key ownership and rotation;
- provider training and retention opt-out terms;
- access reviews for reviewers and database operators;
- redaction or tokenization before provider transmission.

The reference application intentionally does not claim compliance with a named regulatory regime.

## Security verification backlog

- Add integration tests with a disposable JWKS server and expired/wrong-audience tokens.
- Add request rate limiting at the deployment edge.
- Add webhook replay-window semantics in addition to idempotency.
- Export audit events to immutable storage.
- Add software bill of materials, image scanning, and signed provenance in release CI.
- Evaluate model and retrieval behavior using a curated adversarial corpus.
