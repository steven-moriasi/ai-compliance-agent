# Operational runbook

## Local startup

Requirements: Docker Engine with Compose v2.

```bash
docker compose up --build -d
docker compose ps
curl --fail http://localhost:8001/health
curl --fail http://localhost:8001/ready
```

The default stack starts PostgreSQL, migrations, API, analysis worker, and lease reaper. The API is
published on port `8001` by default because port `8000` is commonly occupied. Override it with
`COMPLIANCE_API_PORT`.

Start signed webhook delivery only after configuring the required environment variables:

```bash
COMPLIANCE_NOTIFICATION_WEBHOOK_URL=https://consumer.example/reviews \
COMPLIANCE_NOTIFICATION_WEBHOOK_SECRET='managed-secret-value' \
docker compose --profile notifications up -d notifier
```

Do not use the example database password or development-header authentication outside a local
environment.

## Local development

```bash
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

Run background processes separately:

```bash
python -m app.worker
python -m app.reaper
python -m app.notifier
```

The notifier exits immediately unless both webhook settings are present.

## Verification

```bash
ruff check app tests evals alembic ml
mypy app evals ml
python -m evals.run evals/cases/v1.json
pytest --cov=app --cov-report=term-missing --cov-fail-under=85
docker build -t ai-compliance-agent:local .
docker compose config --quiet
```

Verify a migration round trip against a disposable PostgreSQL database:

```bash
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

Never run a downgrade against production merely as a health check. Validate backups and the exact
release procedure before a production schema change.

Run concurrency evidence only against a disposable PostgreSQL database:

```bash
COMPLIANCE_TEST_DATABASE_URL=postgresql+psycopg://compliance:compliance@localhost:5432/compliance \
pytest -m postgres tests/test_postgres_concurrency.py
```

The test fixture creates and removes an isolated schema. It verifies competing `SKIP LOCKED`
claims, stale analysis fencing after a reap, and notification reclaim. It skips when the test
database URL is absent.

Run the deterministic corpus after changing retrieval, provider output, citation validation, retry
behavior, or failure handling:

```bash
python -m evals.run evals/cases/v1.json
```

## Health and telemetry

| Endpoint | Meaning |
| --- | --- |
| `GET /health` | process is serving HTTP |
| `GET /ready` | process can execute a database query |
| `GET /metrics` | Prometheus counters and model-latency histogram |

Core metrics:

- `compliance_analysis_total{status,provider}`;
- `compliance_analysis_latency_seconds{provider}`;
- `compliance_review_total{decision}`.

Recommended deployment alerts:

- readiness failures;
- oldest queued case age;
- expired or exhausted analysis attempts;
- provider failure ratio and latency;
- cases awaiting review beyond the business objective;
- notification retry age and dead-letter count;
- PostgreSQL connection saturation and storage growth.

The last four require database-derived or platform metrics beyond the current process-local
Prometheus endpoint.

## Triage commands

```bash
docker compose ps
docker compose logs --tail=200 api
docker compose logs --tail=200 worker
docker compose logs --tail=200 reaper
docker compose --profile notifications logs --tail=200 notifier
docker compose exec postgres pg_isready -U compliance -d compliance
```

Avoid copying source documents, bearer tokens, provider responses, or secret-bearing database URLs
into tickets.

## Incident: queue is not draining

1. Confirm PostgreSQL and the worker are healthy.
2. Check whether cases are `QUEUED`, `ANALYZING`, or `FAILED`.
3. Compare analysis latency with the configured lease duration.
4. Inspect audit events for `analysis_requeued`, `analysis_abandoned`, or `analysis_failed`.
5. Validate model-provider reachability and quota without logging the API key.
6. Scale workers only if database connections and provider quota permit.
7. If leases expire during valid work, raise the lease or add renewal rather than disabling fencing.

Do not manually mark a case approved or bypass review to clear a queue.

## Incident: cases repeatedly expire

1. Confirm worker processes are not restarting or being terminated.
2. Check provider latency and network timeouts.
3. Confirm host clocks are synchronized.
4. Compare `analysis_lease_seconds` with observed analysis duration. One SQLite keyword query over the loaded corpus took 124411 ms (`evals/reports/keyword_retrieval.json`). The default lease is 120 seconds, so that query does not finish inside the default lease. That file is one observation, not a percentile. On PostgreSQL, keyword p50 over 10 titles was 34808.43209999148 ms and full-text p95 over 50 titles was 131.6251999232918 ms (`evals/reports/retrieval_latency_2026-10-07.json`). Full text is the PostgreSQL default.
5. Inspect attempt counts and preserve exhausted cases as evidence.
6. Submit a new case only after correcting the underlying condition.

## Incident: model output is rejected

1. Identify whether the failure is transport, empty response, JSON parsing, or schema validation.
2. Confirm the configured prompt's `output_schema_version` matches the deployed application.
3. Verify the provider supports strict JSON-schema response formatting.
4. Reproduce with non-sensitive fixture content.
5. Roll back the model or prompt configuration if compatibility changed.

Never weaken the application schema or citation validation merely to accept a provider response.

## Incident: suspicious prompt injection

1. Keep the case in human review.
2. Restrict document access to the assigned reviewer and incident responders.
3. Review the exact retrieved policy versions and validation findings.
4. Reject the case if the result cannot be supported independently from the source material.
5. Add a sanitized pattern to the adversarial evaluation corpus.
6. Assess whether similar documents or model outputs were processed.

Prompt-injection detection is a signal, not proof that a document is malicious or safe.

## Incident: notifications are not delivered

1. Confirm the notifier profile is running and its settings are present.
2. Check endpoint DNS, TLS, and response status without printing the signing secret.
3. Inspect outbox status, attempts, `available_at`, and `last_error`.
4. Confirm the consumer deduplicates on `Idempotency-Key`.
5. Rotate the webhook secret if compromise is suspected and coordinate consumer rollout.
6. Repair the endpoint before deliberately re-driving dead-letter records.

Never change a `DEAD` row directly without recording an operator action and preserving the original
attempt evidence.

## Incident: identity provider unavailable

Protected operations fail closed when JWKS retrieval or token validation fails. Confirm issuer,
audience, JWKS URL, DNS, TLS, and key rotation. Do not switch a non-development deployment to
development authentication as a workaround.

## Cost controls

Each case stores input tokens, output tokens, latency, provider, model, and estimated cost based on
configured per-million-token prices. Treat the estimate as operational telemetry, not an invoice.

Before enabling a remote model:

- set current input and output price configuration;
- enforce provider-side budget and rate limits;
- cap document size and retrieved context;
- alert on token and cost growth;
- compare provider invoices against stored estimates;
- define whether retries may issue another billable request.

## Shutdown and cleanup

```bash
docker compose down
```

To delete local database data as well:

```bash
docker compose down -v
```

Deleting the volume is destructive and must not be used for an environment containing evidence that
must be retained.
