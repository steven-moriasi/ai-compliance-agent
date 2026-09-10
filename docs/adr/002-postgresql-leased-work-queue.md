# ADR 002: Use PostgreSQL as a leased work queue

- Status: Accepted
- Date: 2026-09-10

## Context

Analysis requests must survive API and worker restarts. The reference implementation should
demonstrate concurrency safety and recovery without requiring a separate broker for a modest
workflow.

## Decision

Store case state in PostgreSQL and claim queued work with `FOR UPDATE SKIP LOCKED`. Each claim:

- moves the case to `ANALYZING`;
- records a worker identity;
- increments an attempt counter and fencing token;
- assigns a bounded lease.

Finalization requires the same worker, token, state, and an unexpired lease. A separate reaper
requeues expired work or fails it after a configured attempt limit.

## Consequences

- Workflow state and queue state share one transactional system.
- Horizontal workers can claim independent rows without a central scheduler.
- A stale process cannot overwrite work completed after recovery.
- Model calls may repeat after lease expiry and must be side-effect free.
- Polling adds database load and is less efficient than broker push at high scale.
- Lease selection depends on provider latency and clock behavior.

## Alternatives considered

- **In-memory background tasks:** rejected because accepted work disappears on process failure.
- **Redis or a message broker:** valid at larger scale, but adds an operational system without
  removing the need for durable domain state and fencing.
- **Database status without leases:** rejected because crashed workers would strand cases
  indefinitely.
