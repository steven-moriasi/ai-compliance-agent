# ADR 006: Retry model-fixable output once

- Status: Accepted
- Date: 2026-09-27

## Context

A provider can return valid transport responses that fail the required schema or contain citations
that deterministic validation can identify as repairable. Retrying indefinitely would increase
cost and latency, obscure evidence, and allow poison cases to occupy workers. Treating every
validation finding as retryable would also misrepresent confidence and prompt-injection signals as
formatting mistakes.

## Decision

Allow at most two provider attempts per claimed case.

Retry once when the first response:

- is malformed or fails the strict output schema; or
- has a retrieved-source, exact-quote, minimum-quote-length, or cited-temporal-claim finding.

Provide stable validation feedback codes to the second request. When the current fenced claimant
finalizes, persist an `analysis_attempted` audit event for each attempt and accumulate token and
latency usage across valid responses.

Do not invoke or retry the provider when no relevant source is retrieved. Do not retry transport or
other provider failures inside the analysis service. Do not retry low confidence or
prompt-injection signals; they remain findings for the mandatory reviewer.

After the second malformed response, route the case to review with
`malformed_model_output`. After a second structurally valid but unsupported response, persist its
validation findings and route it to review. Worker lease recovery remains separate and may repeat
the whole analysis under a new fencing token.

## Consequences

- A model gets one bounded opportunity to repair deterministic output defects.
- Validation feedback is machine-readable and testable.
- Cost and latency are bounded within one worker claim.
- Audit events distinguish attempts and final workflow outcomes.
- Provider and transport failures fail the case immediately and rely on explicit resubmission or
  future workflow policy rather than hidden retries.
- Lease expiry can still cause another provider call; provider analysis must have no external side
  effects.

## Alternatives considered

- **Never retry:** rejected because a single schema or citation repair can produce reviewable
  evidence without operator resubmission.
- **Retry all findings:** rejected because low confidence and injection signals are not reliably
  repairable by asking the same model again.
- **Unbounded or exponential retries:** rejected because they create unpredictable cost, latency,
  and queue occupancy.
- **Provider-SDK retries for every error:** rejected because transport retry semantics differ from
  deterministic output correction and should remain explicit.
