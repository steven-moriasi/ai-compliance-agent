# ADR 001: Use a strict structured model-provider boundary

- Status: Accepted
- Date: 2026-09-10

## Context

Compliance analysis must be testable without credentials and must not trust free-form model text.
The application also needs a replaceable boundary for remote providers without coupling domain
logic to one SDK.

## Decision

Define a small synchronous `ModelProvider` protocol that accepts the system prompt, untrusted
document, and retrieved policy records. Require a `ModelAnalysis` response containing a bounded
outcome, confidence, rationale, and typed citations.

Ship:

- a deterministic local provider for tests and demonstrations;
- an OpenAI-compatible HTTP provider using strict JSON-schema response formatting;
- Pydantic validation at the provider boundary;
- normalized token and latency metadata.

## Consequences

- Tests remain deterministic and credential-free.
- Domain services are independent of a vendor SDK.
- Invalid or empty provider responses fail before entering workflow state.
- A new provider needs an adapter and compatibility tests.
- The synchronous call consumes a worker for the duration of provider latency.
- JSON-schema support is a capability requirement for the remote adapter.

## Alternatives considered

- **Free-form text parsing:** rejected because parsing ambiguity is incompatible with an auditable
  control path.
- **Direct vendor SDK calls in the workflow:** rejected because it couples domain logic, retries,
  and telemetry to a vendor-specific interface.
- **Mock the remote provider only:** rejected because a real deterministic implementation provides
  a runnable local workflow rather than tests that merely imitate one.
