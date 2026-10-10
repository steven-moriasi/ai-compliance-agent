# AI Compliance Agent

A production-oriented reference implementation of a human-reviewed compliance analysis workflow.
It places an LLM behind deterministic retrieval, structured output, citation validation, confidence
controls, durable processing, and an immutable review boundary instead of presenting a chatbot as
an enterprise control.

```text
document -> hash -> policy retrieval -> structured model analysis
         -> deterministic validation -> human review -> audit + signed notification
```

This project demonstrates architecture and engineering practices. It does not provide legal advice,
replace compliance professionals, claim regulatory certification, or reproduce a client system.

## Why this is not a chatbot

The model has no authority to mutate policies or finalize decisions. Every completed analysis:

1. uses a versioned prompt and policy catalog;
2. retrieves policy sections whose effective window contains the case date;
3. returns a strict typed result with section-qualified source citations;
4. passes deterministic citation and confidence checks;
5. records prompt-injection signals;
6. transitions to mandatory human review;
7. becomes approved or rejected only by an authorized reviewer;
8. binds the review to a hash of the exact analysis snapshot.

## Engineering evidence

| Concern | Implementation |
| --- | --- |
| Reproducibility | deterministic local provider, pinned direct dependencies, Docker and Compose |
| Structured AI | strict JSON-schema request and Pydantic response validation |
| Grounding | effective-dated section retrieval and exact-quote validation within the cited section |
| Retrieval | PostgreSQL hybrid (full text + MiniLM in pgvector); keyword overlap remains the SQLite baseline |
| Hallucination mitigation | retrieved-source checks, confidence finding, mandatory review |
| Grounded answers | numbered sources, word-for-word quote checks, unanswered when unsupported |
| Regression evidence | versioned deterministic corpus for retrieval, citations, retries, and failures |
| Prompt injection | untrusted-document envelope, signal detection, no model-controlled actions |
| Durable execution | PostgreSQL queue, worker leases, reaper, fencing tokens, concurrency tests |
| Idempotency | database-enforced case key and webhook delivery ID |
| Human accountability | reviewer RBAC, rationale, analysis hash, unique immutable review |
| Side effects | transactional outbox, HMAC-signed webhook, retries, dead-letter state |
| Security | OIDC/JWKS validation, RBAC, byte limits, redacted provider failures |
| Observability | health/readiness, Prometheus metrics, token/latency/cost persistence, audit events |
| Delivery | Alembic round trips, coverage gate, pinned CI actions, non-root container |

## Architecture

```mermaid
flowchart LR
    Client[Admin / analyst / reviewer] --> API[FastAPI]
    API --> DB[(PostgreSQL)]
    Worker[Analysis worker] --> DB
    Worker --> Retrieval[Policy retrieval]
    Retrieval --> DB
    Worker --> Model[Deterministic or remote model]
    Reaper[Lease reaper] --> DB
    Notifier[Outbox notifier] --> DB
    Notifier --> Consumer[Signed webhook consumer]
```

See:

- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Workflow and recovery sequences](docs/SEQUENCES.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Failure model](docs/FAILURE_MODEL.md)
- [Operational runbook](docs/RUNBOOK.md)
- [Architecture decisions](docs/adr)
- [Risk-driven roadmap](docs/ROADMAP.md)

## Reading order

Start with this README, then review the architecture and workflow sequences. Continue with the
threat model, failure model, operational runbook, architecture decisions, and roadmap.

## Limits

- The repository does not establish production scale, availability, recovery, or incident outcomes.
- It does not establish legal or regulatory correctness, certification, or formal assurance.
- Retrieval quality is measured on one Federal Register corpus and two evaluation sets
  ([ADR 008](docs/adr/008-hybrid-retrieval-default.md)), not in general. Answer quality and fairness
  are not established; `python -m evals.answer_eval` measures answers on a loaded corpus.
- Exact quote presence does not prove that a model or reviewer interpreted the source correctly.
- Federal Register rules can be loaded into the catalog. The loader does not prove that a cached
  file is the authentic GovInfo publication.
- The case date is currently the case creation date, not a separately supplied legal or business
  applicability date.
- It makes no claim about client history, cost savings, adoption, or business outcomes.

## Quick start

Requirements: Docker Engine with Compose v2.

```bash
docker compose up --build -d
docker compose ps
curl --fail http://localhost:8001/health
curl --fail http://localhost:8001/ready
```

The default stack starts:

- PostgreSQL 17 with pgvector (`pgvector/pgvector:pg17`), published on `127.0.0.1:5432`;
- one-shot Alembic migrations;
- FastAPI;
- an analysis worker;
- an expired-lease reaper.

Webhook delivery is an opt-in profile because it requires an external endpoint and signing secret.
See the [runbook](docs/RUNBOOK.md) for startup and incident procedures.

`docker compose up --build -d` is the local stack. With `COMPLIANCE_RETRIEVAL_MODE` unset,
PostgreSQL search uses hybrid retrieval and SQLite keeps keyword overlap. Hybrid needs the
`retrieval` extra and a built `section_embeddings` index. Analysis falls back to full text when
either is missing and records a `retrieval_fallback` audit event. The worker loads the embedding
model once at startup, so the first start after installing the extra also downloads the pinned
revision. Load the cached Federal Register window after migrations, then embed it:

```bash
set COMPLIANCE_DATABASE_URL=postgresql+psycopg://compliance:compliance@127.0.0.1:5432/compliance
alembic upgrade head
py -3 -m app.ingestion.federal_register ingest
py -3 -m pip install -e '.[retrieval]' --extra-index-url https://download.pytorch.org/whl/cpu
py -3 -m app.ingestion.embed
```

On this host that load took 130502 ms and wrote dataset `840a6be0a156`
(`evals/reports/federal_register_corpus_postgres.json`). Embedding all 34174 sections took
5422.5 seconds at 6.1 sections per second, and 24272 sections were
truncated at 256 tokens (`evals/reports/embedding_index_2026-10-07.json`). Full-text p95 over
50 policy titles was 131.6 ms
(`evals/reports/retrieval_latency_2026-10-07.json`). Vector p95 on the same titles was
236.1 ms; hybrid p95 was 1594.2 ms
(`evals/reports/retrieval_latency_vector_2026-10-07.json`). The mode comparison that selected
hybrid is in `evals/reports/retrieval_eval_2026-10-07.json` and
[ADR 008](docs/adr/008-hybrid-retrieval-default.md). Keyword search on the same database is
still the slow baseline.

The CFR-part comparison on that dataset is `evals/reports/cfr_classifier_2026-10-07.json`.
On 99 held-out documents the MLP top-1 hit rate is 0.838, the same as naive Bayes
and below TF-IDF logistic regression at 0.848. Gold population stability is
0.688. The promotion gate refused the MLP
([ADR 009](docs/adr/009-cfr-classifier-not-promoted.md)).

Stop the stack:

```bash
docker compose down
```

## Local development

Supported Python versions are 3.11 through 3.14; CI uses Python 3.12.

```bash
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

Run background processes in separate terminals:

```bash
python -m app.worker
python -m app.reaper
python -m app.notifier
```

SQLite is the credential-free default for local tests. PostgreSQL is the reference durable runtime.

## API surface

| Method | Endpoint | Roles | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/policies` | admin | create a hashed policy version |
| `GET` | `/api/v1/policies` | any application role | list policy versions |
| `POST` | `/api/v1/prompts` | admin | create and optionally activate a prompt version |
| `GET` | `/api/v1/prompts` | any application role | list prompt versions |
| `POST` | `/api/v1/documents` | admin, analyst | ingest and hash a bounded document |
| `POST` | `/api/v1/cases` | admin, analyst | create an idempotent asynchronous analysis |
| `GET` | `/api/v1/cases/{id}` | any application role | read workflow and model evidence |
| `POST` | `/api/v1/cases/{id}/reviews` | admin, reviewer | approve or reject with rationale |
| `GET` | `/api/v1/cases/{id}/audit` | any application role | read ordered case audit events |
| `POST` | `/api/v1/questions` | any application role | queue a question about policies in force on a date |
| `GET` | `/api/v1/questions/{id}` | the asker, admin | read the answer, its quotes, and the sources it was given |
| `GET` | `/health` | public | process liveness |
| `GET` | `/ready` | public | database readiness |
| `GET` | `/metrics` | public in reference app | Prometheus exposition |

Production deployments should restrict operational endpoints at the network edge.

### Development authentication

Local development accepts `X-User-ID` and comma-separated `X-Roles` headers. If omitted, the
development default is a local administrator with all roles. This mode fails closed whenever
`COMPLIANCE_ENVIRONMENT` is not `development`.

### Production authentication

Set OIDC mode and configure issuer, audience, and JWKS URL. Bearer tokens are verified with RS256,
issuer, audience, and provider signing keys. Roles are read from the `roles` list and space-separated
`scope` claim.

## Model providers

### Deterministic

The default provider requires no credential. It selects retrieved policy evidence and returns a
stable review-required result, making the entire workflow and tests reproducible.

### OpenAI-compatible

The remote adapter sends:

- a versioned system prompt;
- an explicitly labeled untrusted document;
- retrieved policy IDs, names, versions, section references, headings, and section text;
- the generated strict JSON schema for `ModelAnalysis`.

Configure the provider only through environment or managed deployment secrets. Provider errors are
stored as stable redacted application errors rather than raw response text.

### Local model

`COMPLIANCE_MODEL_PROVIDER=ollama` sends the same requests to Ollama's OpenAI-compatible endpoint,
with the schema in the system prompt because small models are asked for a JSON object rather than
a strict schema. Nothing leaves the machine.

```bash
ollama pull qwen2.5:3b
set COMPLIANCE_MODEL_PROVIDER=ollama
py -3 -m app.worker
```

On a laptop CPU a 3B model takes from about half a minute to a few minutes per call, which is why
cases and questions run on the worker rather than inside an HTTP request.

## Asking the policies

The demo page's **Ask the policies** panel, and `POST /api/v1/questions`, answer a question from
the policy sections in force on a chosen date:

1. The worker retrieves the top sections with the configured mode (hybrid on PostgreSQL).
2. Each section is cut at a word boundary to `QUESTION_SOURCE_CHARS` and numbered.
3. The model returns JSON: whether the sources answer the question, the answer, and for each
   statement a source number and a quote.
4. Every quote must appear word for word in the source it names, and every date or duration in
   the answer must sit inside a quote. A failed check gets one retry with the failures spelled
   out; a second failure leaves the question unanswered.
5. The reader sees either a checked answer with its quotes and Federal Register links, or
   "no supported answer" with the sources the model was given.

The question is redacted before the model sees it, and instruction-like text is flagged. The model
is never trained on the policies; it reads the retrieved sections on every question. See
[ADR 010](docs/adr/010-grounded-policy-answers.md).

Ollama's OpenAI-compatible endpoint cannot set the context window per request, and it truncates an
overflowing prompt without an error. Four sources of 1,500 characters and the instructions fit a
4,096-token window with room for the answer. If you raise either limit, raise
`OLLAMA_CONTEXT_LENGTH` on the Ollama server as well.

Measure answers on the loaded corpus with the model you intend to use:

```bash
set COMPLIANCE_MODEL_PROVIDER=ollama
py -3 -m evals.answer_eval
```

`evals/answers/questions_v1.jsonl` holds in-scope questions with the Federal Register documents
that should be cited, questions dated before their document took effect, and out-of-scope
questions that should go unanswered. The report lands in `evals/reports/answer_eval.json`.

## Configuration

All variables use the `COMPLIANCE_` prefix.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | local SQLite | SQLAlchemy database URL |
| `ENVIRONMENT` | `development` | enables local-only authentication guard |
| `AUTH_MODE` | `development` | `development` or `oidc` |
| `OIDC_ISSUER` | unset | expected token issuer |
| `OIDC_AUDIENCE` | unset | expected token audience |
| `OIDC_JWKS_URL` | unset | signing-key endpoint |
| `MODEL_PROVIDER` | `deterministic` | provider adapter |
| `MODEL_BASE_URL` | OpenAI API URL | remote provider base URL |
| `MODEL_NAME` | `gpt-4.1-mini` | remote model identifier |
| `MODEL_API_KEY` | unset | remote provider credential |
| `MODEL_INPUT_COST_PER_MILLION` | `0` | estimate configuration |
| `MODEL_OUTPUT_COST_PER_MILLION` | `0` | estimate configuration |
| `CONFIDENCE_THRESHOLD` | `0.7` | validation finding threshold |
| `MAX_DOCUMENT_BYTES` | `262144` | ingestion byte limit |
| `ANALYSIS_LEASE_SECONDS` | `120` | worker lease duration |
| `RETRIEVAL_MODE` | unset | `keyword`, `fulltext`, `vector`, `embedding`, or `hybrid`; unset uses hybrid on PostgreSQL and keyword elsewhere |
| `LOCAL_MODEL_BASE_URL` | `http://127.0.0.1:11434/v1` | Ollama's OpenAI-compatible endpoint |
| `LOCAL_MODEL_NAME` | `qwen2.5:3b` | local model tag |
| `LOCAL_MODEL_TIMEOUT_SECONDS` | `180` | per-call timeout for the local model |
| `QUESTION_SOURCE_LIMIT` | `4` | sections given to the model per question |
| `QUESTION_SOURCE_CHARS` | `1500` | characters kept from each section |
| `ANALYSIS_MAX_ATTEMPTS` | `3` | terminal attempt limit |
| `NOTIFICATION_WEBHOOK_URL` | unset | review event destination |
| `NOTIFICATION_WEBHOOK_SECRET` | unset | HMAC signing secret |
| `NOTIFICATION_LEASE_SECONDS` | `60` | notifier lease duration |
| `NOTIFICATION_MAX_ATTEMPTS` | `5` | dead-letter attempt limit |

`.env.example` documents local configuration names. Its values are not production credentials.

## Quality gates

```bash
ruff check app tests evals alembic ml
mypy app evals ml
python -m evals.run evals/cases/v1.json
pytest --cov=app --cov-report=term-missing --cov-fail-under=85
docker build -t ai-compliance-agent:local .
docker compose config --quiet
```

### Verification checks

| Check | What it verifies | What it does not prove |
| --- | --- | --- |
| Ruff and mypy | source quality rules and strict application/evaluation typing | runtime behavior |
| Deterministic evaluations | expected retrieval, citation, retry, injection, and failure outcomes | legal correctness or live-model quality |
| Pytest and coverage | API, workflow, provider, validation, recovery, and authorization behavior | production scale or availability |
| PostgreSQL concurrency tests | `SKIP LOCKED` claims, stale-token fencing, and notification reclaim | throughput under sustained load |
| Alembic round trip | upgrade, downgrade, and re-upgrade execute on PostgreSQL | zero-downtime release compatibility |
| Container and Compose checks | the image builds and local topology is structurally valid | a production deployment |

CI runs the PostgreSQL concurrency tests after the migration round trip. The standard test suite
skips those tests unless `COMPLIANCE_TEST_DATABASE_URL` points to a disposable PostgreSQL database.
No verification command requires a real model API key.

### Deterministic evaluation corpus

Run the versioned corpus directly:

```bash
python -m evals.run evals/cases/v1.json
pytest tests/test_evals.py
```

Each case declares a case date, policy versions and sections, deterministic provider outputs, and
expected retrieved sources, validation findings, workflow status, provider attempts, and audit
events. Add a sanitized case whenever retrieval, validation, retry, or failure behavior changes.
The corpus uses SQLite and local fixtures; it makes no network calls.

Host, Federal Register, and keyword-retrieval figures are recorded in
[docs/MEASUREMENTS.md](docs/MEASUREMENTS.md). That page quotes `evals/reports/` and does not fill
gaps where a report was not written.

Run the PostgreSQL-only evidence against a disposable database:

```bash
COMPLIANCE_TEST_DATABASE_URL=postgresql+psycopg://compliance:compliance@localhost:5432/compliance \
pytest -m postgres tests/test_postgres_concurrency.py
```

## Repository structure

```text
app/api/             HTTP endpoints and role boundaries
app/core/            configuration and Prometheus metrics
app/domain/          persistence models, enums, and API/model schemas
app/infrastructure/  database sessions and authentication
app/services/        retrieval, providers, validation, workflow, audit, outbox
alembic/              versioned database migrations
evals/                 versioned verification cases, retrieval and answer evaluations
tests/                 workflow, security, provider, recovery, and PostgreSQL concurrency tests
docs/                  architecture, security, operations, decisions, and roadmap
```

## License

[MIT](LICENSE)
