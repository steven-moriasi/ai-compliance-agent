# Failure model

## Design principle

The workflow prefers a visible, reviewable stop over an unsupported automated conclusion. Work is
accepted asynchronously, state transitions are persisted in PostgreSQL, and side effects are
decoupled through an outbox.

## Failure catalogue

| Failure | Detection | System behavior | Recovery | Data-consistency property |
| --- | --- | --- | --- | --- |
| Duplicate case request | existing unique key or insert conflict | returns original case with replay header | caller continues with returned case | one case per document and idempotency key |
| Missing/inactive prompt | API precondition | rejects request with 404/409 | administrator creates or activates a version | no case starts without a known prompt |
| No matching active policy | deterministic provider error | case becomes failed with stable error code | activate applicable policy and submit a new case | no uncited result is persisted |
| Provider timeout/non-2xx | HTTP exception | case becomes failed under active lease | investigate provider; submit new case | raw provider data is not exposed |
| Malformed model output | strict Pydantic validation error | case becomes failed under active lease | correct provider/schema compatibility | invalid structure cannot reach review |
| Unsupported citation | deterministic validation | finding recorded; review still required | reviewer rejects or handles exception | model cannot invent a trusted citation silently |
| Low confidence | configured threshold | finding recorded; review required | reviewer decides with source context | confidence never bypasses review |
| Prompt-injection signal | lexical signal detector | finding recorded; review required | reviewer treats document as adversarial | model text cannot directly change control flow |
| Worker termination | lease expires | case remains `ANALYZING` until reaped | reaper requeues below attempt limit | work is not acknowledged before durable result |
| Worker exceeds lease | conditional update affects zero rows | late result is discarded | new worker processes requeued case | fencing prevents stale overwrite |
| Repeated worker failure | attempt counter reaches limit | case becomes `FAILED` and audited | operator investigates before resubmission | poison work does not loop forever |
| Concurrent review | row lock and status check | first review wins; later request conflicts | later reviewer reads final state | one immutable review per case |
| API failure before commit | database transaction rolls back | review, audit, and notification are absent | retry review request | final state and outbox are atomic |
| API failure after commit | caller may miss response | committed review remains visible | caller reads case; duplicate review conflicts | no duplicate review side effect |
| Notifier termination | delivery lease expires | record remains `DELIVERING` until reclaimed | another notifier reclaims it | at-least-once attempt semantics |
| Webhook timeout | HTTP exception | retry scheduled with exponential backoff | consumer deduplicates by notification ID | outcome may be ambiguous; no false `SENT` |
| Repeated webhook failure | attempt limit | record moves to `DEAD` | operator repairs endpoint and re-drives deliberately | bounded retries preserve evidence |
| Database unavailable | readiness/operations fail | API readiness fails; workers cannot claim | restore database connectivity | no in-memory fallback or split state |
| OIDC/JWKS unavailable | token validation fails | protected API calls fail closed | restore IdP/JWKS access | no authentication bypass |

## Delivery semantics

### Case analysis

Case analysis is effectively at-least-once execution with single-writer finalization. A process may
call the model more than once after lease recovery, but only the current fencing token may persist
the result. Provider calls must therefore be treated as potentially repeated and should not carry
external side effects.

### Notifications

Notification delivery is at least once. A timeout can occur after the consumer accepted a request
but before the notifier observed the response. The consumer must use `Idempotency-Key` as a stable
deduplication key. Marking a row `SENT` is conditional on the current fencing token and lease.

### Reviews

Reviews are single-assignment. The API locks the case, requires `REVIEW_REQUIRED`, and writes a
unique review record. The final case transition, review record, audit event, and outbox enqueue
share one transaction.

## Time and lease assumptions

Leases currently use application wall-clock time. This is acceptable for the single-region
reference topology. A distributed production design should:

- use database time for claim and finalization comparisons;
- maintain synchronized hosts;
- choose a lease longer than the expected high-percentile provider latency;
- add lease renewal for analyses that may legitimately exceed that duration;
- monitor clock skew and lease-expiration rates.

## Error disclosure

Persistent and API-visible errors use stable codes and generic messages. Provider response bodies,
credentials, document excerpts, and exception strings must remain out of public errors and routine
logs. Detailed diagnostics belong in access-controlled telemetry with redaction.

## Capacity and backpressure

The PostgreSQL queue is intentionally simple. Capacity limits are reached when queue age, database
lock contention, provider quotas, or notification retries grow faster than workers can drain them.
Scale workers horizontally only after validating provider quotas and database connection limits.
Use queue age rather than raw queue depth as the primary user-impact signal.

## Recovery objectives

No numerical recovery target is claimed by this reference implementation. A deployment owner
should define:

- maximum acceptable case queue age;
- database recovery point and recovery time objectives;
- maximum analysis-attempt age before operator intervention;
- notification delivery objective and dead-letter response time;
- audit evidence retention and restore validation frequency.
