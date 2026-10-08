# Workflow sequences

## Analyze and review a document

```mermaid
sequenceDiagram
    autonumber
    actor Analyst
    actor Reviewer
    participant API
    participant DB as PostgreSQL
    participant Worker
    participant Model
    participant Notifier
    participant Consumer as Webhook consumer

    Analyst->>API: Create document
    API->>DB: Insert document + document_ingested audit
    Analyst->>API: Create case + X-Idempotency-Key
    API->>DB: Insert queued case + case_requested audit
    API-->>Analyst: 202 Accepted

    Worker->>DB: Claim oldest queued case + analysis_started audit
    DB-->>Worker: Lease + incremented fencing token
    Worker->>DB: Load prompt + policy sections effective on case date
    Worker->>Worker: Rank sections and detect injection signals
    Worker->>Model: Prompt + untrusted document + retrieved sections + JSON schema
    Model-->>Worker: Structured analysis, citations, usage
    Worker->>Worker: Validate schema, confidence, and exact-section quotes
    opt Malformed or fixable citation output on first attempt
        Worker->>Model: Retry once with validation feedback
        Model-->>Worker: Corrected structured analysis
    end
    Worker->>DB: Conditional result update under lease
    Worker->>DB: Append analysis_completed audit

    Reviewer->>API: Read case and audit trail
    Reviewer->>API: Approve or reject with rationale
    API->>DB: Lock case and verify REVIEW_REQUIRED
    API->>DB: Insert review bound to analysis hash
    API->>DB: Update final case state
    API->>DB: Append audit + enqueue outbox atomically
    API-->>Reviewer: 201 Created

    Notifier->>DB: Claim pending outbox record
    Notifier->>Consumer: Signed JSON + Idempotency-Key
    Consumer-->>Notifier: 2xx
    Notifier->>DB: Conditional SENT transition
```

## Idempotent case submission

```mermaid
sequenceDiagram
    actor Analyst
    participant API
    participant DB as PostgreSQL

    Analyst->>API: POST case with document D and key K
    API->>DB: Find (document_id=D, idempotency_key=K)
    alt Existing case
        DB-->>API: Existing case
        API-->>Analyst: Existing representation + Idempotent-Replay=true
    else New case
        API->>DB: Insert unique (D, K)
        alt Concurrent request inserted first
            DB-->>API: Unique constraint violation
            API->>DB: Roll back and read winning case
            API-->>Analyst: Existing representation + Idempotent-Replay=true
        else Insert succeeds
            API-->>Analyst: 202 Accepted
        end
    end
```

## Expired analysis lease

```mermaid
sequenceDiagram
    participant WorkerA as Worker A
    participant DB as PostgreSQL
    participant Reaper
    participant WorkerB as Worker B

    WorkerA->>DB: Claim case with token 1 and lease
    Note over WorkerA: Process stops or exceeds lease
    Reaper->>DB: Find expired ANALYZING case
    Reaper->>DB: Requeue + analysis_requeued audit
    WorkerB->>DB: Claim case with token 2 and new lease
    WorkerB->>DB: Finalize where token=2 and lease active
    DB-->>WorkerB: One row updated
    WorkerA->>DB: Late finalize where token=1
    DB-->>WorkerA: Zero rows updated
```

## Provider failure

```mermaid
sequenceDiagram
    participant Worker
    participant Provider
    participant DB as PostgreSQL

    Worker->>Provider: Structured analysis request
    Provider--xWorker: HTTP, schema, or empty-result failure
    Worker->>DB: Conditional FAILED transition under active lease
    Worker->>DB: Store stable error code and redacted message
    Worker->>DB: Append analysis_failed audit
```

The API does not expose raw provider exceptions, response bodies, or credentials.

## Answer a policy question

```mermaid
sequenceDiagram
    actor Reader
    participant API
    participant DB as PostgreSQL
    participant Worker
    participant Model

    Reader->>API: POST /api/v1/questions (question, as_of)
    API->>DB: Insert queued question + question_asked audit
    API-->>Reader: 202 with question id
    Worker->>DB: Claim oldest queued question + question_started audit
    Worker->>DB: Retrieve sections effective on as_of
    Worker->>Model: Redacted question + numbered, trimmed sources + answer schema
    Model-->>Worker: supported flag, answer, source numbers and quotes
    Worker->>Worker: Check each quote word for word in its source
    alt a check fails on the first attempt
        Worker->>Model: Retry once with the failures as instructions
        Model-->>Worker: Revised answer
    end
    Worker->>DB: ANSWERED or UNANSWERED under the lease + audit with cited sources
    Reader->>API: GET /api/v1/questions/{id} until it finishes
```

An unanswered question still returns the sources the model was given.

## Notification retry and dead letter

```mermaid
sequenceDiagram
    participant Notifier
    participant DB as PostgreSQL
    participant Consumer

    Notifier->>DB: Claim notification and increment attempt/token
    Notifier->>Consumer: HMAC-signed request
    Consumer--xNotifier: Timeout or non-2xx
    alt Attempts remain
        Notifier->>DB: PENDING with exponential backoff
    else Attempt limit reached
        Notifier->>DB: DEAD with stable error message
    end
```
