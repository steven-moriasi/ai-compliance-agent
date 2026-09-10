# Portfolio evidence map

This map helps a technical reviewer distinguish implemented evidence from design discussion. It is
not a claim that the repository has operated in production.

| Engineering claim | Implementation evidence | Verification evidence |
| --- | --- | --- |
| Design an AI workflow rather than a chatbot | `app/services/analysis.py`, mandatory `REVIEW_REQUIRED` transition | end-to-end human review test |
| Integrate a replaceable LLM boundary | `ModelProvider`, deterministic adapter, OpenAI-compatible adapter | strict request/response provider test |
| Enforce structured model output | `ModelAnalysis` Pydantic schema and remote JSON-schema request | provider contract tests and mypy |
| Ground results in managed policy versions | active-policy retrieval with ID and version citations | retrieval in workflow test |
| Mitigate unsupported model claims | exact-quote and retrieved-source validation | invalid quote and unretrieved policy tests |
| Handle prompt-injection risk | untrusted-document envelope and injection-signal detector | injection workflow test |
| Apply confidence safely | configured threshold creates a validation finding | low-confidence workflow assertion |
| Preserve human accountability | reviewer RBAC, rationale, unique record, analysis hash | approval flow and role rejection tests |
| Provide auditable provenance | document/policy hashes, prompt versions, actors, correlations, audit events | ordered case audit assertion |
| Build durable asynchronous processing | PostgreSQL-backed state machine and worker process | migration and workflow tests |
| Recover from process failure | leases, attempts, reaper, fencing tokens | requeue, stale-worker, and exhaustion tests |
| Make intake idempotent | unique document/key index and conflict recovery | replay header and stable case test |
| Decouple integration side effects | transactional notification outbox | review-to-outbox delivery test |
| Secure webhook delivery | canonical JSON, HMAC-SHA256, stable idempotency key | signature assertion |
| Bound delivery failure | retry backoff, lease reclaim, terminal dead-letter state | retry-to-dead-letter test |
| Enforce enterprise identity boundary | OIDC issuer/audience/JWKS validation and endpoint RBAC | missing bearer and development-mode rejection tests |
| Avoid sensitive provider errors | stable error code and generic persisted message | provider failure redaction test |
| Make cost visible | persisted input/output tokens, latency, and configured cost estimate | provider usage test and workflow persistence |
| Version the database | three Alembic revisions with reversible operations | PostgreSQL upgrade/downgrade/upgrade CI job |
| Package multiple runtime roles | one non-root image; API, worker, reaper, notifier commands | image build and Compose smoke check |
| Apply software delivery controls | Ruff, strict mypy, coverage threshold, pinned GitHub Actions | CI workflow |
| Reason about operational risk | architecture, sequences, threat model, failure model, ADRs, runbook | Principal Engineer self-review |

## Suggested review path

1. Read `README.md` for scope and calibrated claims.
2. Review `docs/ARCHITECTURE.md` and `docs/SEQUENCES.md`.
3. Inspect `app/services/analysis.py`, `providers.py`, `validation.py`, and `retrieval.py`.
4. Inspect `app/services/cases.py` and `notifications.py` for failure semantics.
5. Read `tests/test_workflow.py` and `tests/test_security_and_providers.py`.
6. Compare the implemented controls with `docs/THREAT_MODEL.md`,
   `docs/FAILURE_MODEL.md`, and `docs/PRINCIPAL_ENGINEER_REVIEW.md`.

## What this repository does not prove

- production scale, availability, or incident response;
- legal or regulatory correctness;
- model accuracy, calibration, or fairness;
- semantic retrieval quality;
- certification against a compliance framework;
- experience with a named client or confidential system;
- realized cost savings or business outcomes.

Those claims require external, independently verifiable evidence and are deliberately excluded.
