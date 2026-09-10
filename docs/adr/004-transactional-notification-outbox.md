# ADR 004: Deliver review notifications through a transactional outbox

- Status: Accepted
- Date: 2026-09-10

## Context

A review transaction must not fail because an external webhook is unavailable, and a committed
decision must not lose its notification if the API stops after database commit.

## Decision

Insert a notification-outbox record in the same transaction as the review, final case transition,
and audit event. A separate notifier claims records with leases and fencing tokens, signs canonical
JSON with HMAC-SHA256, and sends the outbox ID as an idempotency key.

Retry failed requests with bounded exponential backoff. Move exhausted notifications to `DEAD`
rather than retrying forever.

## Consequences

- Final decisions and notification intent are atomic.
- External latency does not extend the reviewer transaction.
- Delivery is at least once; consumers must deduplicate.
- Ambiguous timeout outcomes can produce another request.
- Operators need dead-letter monitoring and a controlled re-drive procedure.
- Signing-key rotation requires coordination with consumers.

## Alternatives considered

- **Call the webhook inside the review transaction:** rejected because external availability would
  control database latency and rollback behavior.
- **Fire-and-forget background task:** rejected because process termination can lose committed
  notification intent.
- **Exactly-once delivery claim:** rejected because a network timeout cannot distinguish an
  unprocessed request from a processed response that was lost.
