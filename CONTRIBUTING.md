# Contributing

## Scope

Changes should preserve the central safety property: a model proposes evidence for review but cannot
make a final compliance decision. Do not add claims of legal advice, certification, production
performance, or model accuracy without reproducible evidence.

## Development

Use Python 3.11 through 3.13. Python 3.12 is the CI reference version.

```bash
python -m pip install -e '.[dev]'
alembic upgrade head
```

Before submitting a change:

```bash
ruff check app tests evals alembic
mypy app evals
python -m evals.run evals/cases/v1.json
pytest --cov=app --cov-report=term-missing --cov-fail-under=85
docker build -t ai-compliance-agent:local .
docker compose config --quiet
```

Changes to persistence must include an Alembic migration and a PostgreSQL
upgrade/downgrade/upgrade verification. Changes to AI behavior must include deterministic tests and
must not require a real provider credential.

## Design expectations

- Keep model-provider code behind the typed provider boundary.
- Treat document content and model output as untrusted.
- Preserve exact policy-version provenance and citation validation.
- Keep final decisions behind authorized human review.
- Use idempotency, transactions, leases, and fencing for retryable workflows.
- Keep external side effects outside API transactions through the outbox.
- Use stable public errors and do not expose provider response bodies or secrets.
- Record architectural tradeoffs in an ADR when changing a core boundary.

## Commits and pull requests

Use focused conventional commits such as `feat:`, `fix:`, `test:`, `docs:`, `ci:`, and `chore:`.
Describe why a change is needed, its failure behavior, and how it was verified. Keep unrelated
refactors out of the pull request.
