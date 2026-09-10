# ADR 003: Require human review for every model analysis

- Status: Accepted
- Date: 2026-09-10

## Context

Model confidence is not a calibrated legal or compliance guarantee. Citation validation can prove
that quoted text exists, but not that the model applied it correctly. The repository must not imply
that an LLM is an autonomous compliance authority.

## Decision

Every successful analysis transitions to `REVIEW_REQUIRED`, including results above the confidence
threshold and without detected injection signals. Only an authorized reviewer can transition a case
to `APPROVED` or `REJECTED`.

Persist a single immutable review record containing reviewer identity, rationale, decision, and a
SHA-256 hash of the reviewed analysis snapshot.

## Consequences

- The model remains advisory and cannot create a final decision.
- Every final state has an accountable actor and rationale.
- The analysis hash provides evidence of the exact result presented for review.
- Reviewer capacity becomes a throughput constraint.
- Production deployments need assignment, escalation, and separation-of-duties policies.
- A database administrator can still alter records; immutable external audit export remains a
  production control.

## Alternatives considered

- **Auto-approve above a confidence threshold:** rejected because the score is provider-generated
  and not sufficient evidence of correctness.
- **Review only validation failures:** rejected because absence of detected failure does not prove
  semantic validity.
- **Editable reviews:** rejected because mutation weakens accountability; corrections should be
  represented as a new workflow in a future design.
