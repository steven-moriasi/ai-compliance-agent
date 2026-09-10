# Principal Engineer review

## Review stance

This is a self-review against the standard expected of a production-oriented reference
implementation. It distinguishes code-backed evidence from production controls that are only
documented or planned. It is not an independent endorsement.

## Executive assessment

The repository demonstrates sound boundaries for an AI-assisted compliance workflow:

- the model is advisory rather than authoritative;
- inputs, prompts, policies, analyses, reviews, and side effects have explicit provenance;
- structured output and citation checks constrain model behavior;
- leases, fencing, idempotency, audit events, and an outbox address common distributed failure
  modes;
- local execution, migrations, quality checks, and a multi-process container topology are
  reproducible.

It should not be presented as production-ready. The largest gaps are empirical model evaluation,
tenant/data-governance controls, immutable audit export, richer operational telemetry, and
deployment-platform security.

## Rubric

| Area | Assessment | Evidence | Production gap |
| --- | --- | --- | --- |
| Problem framing | Strong | README scope; mandatory human review; explicit non-goals | business policy and regulatory ownership are deployment-specific |
| Domain model | Strong | versioned policies/prompts, hashed documents, cases, reviews, audit, outbox | no tenant or retention model |
| AI boundary | Strong reference design | provider protocol, strict schema, deterministic provider, token/cost capture | no empirical quality or calibration results |
| Hallucination controls | Good | retrieved policy IDs/versions, exact-quote checks, validation findings | citation existence does not prove interpretation |
| Prompt-injection posture | Good layered baseline | untrusted envelope, signal detection, strict output, mandatory review | detector is intentionally simple; needs adversarial evaluation |
| Reliability | Strong | idempotent intake, PostgreSQL claims, leases, fencing, attempts, reaper | no lease renewal, graceful shutdown, or chaos tests |
| Side effects | Strong | transactional outbox, HMAC, idempotency key, retries, dead letter | no operator re-drive endpoint or replay window |
| Security | Good reference baseline | OIDC/JWKS validation, RBAC, size limits, redacted provider errors | edge controls, secret manager, immutable audit, and data classification absent |
| Observability | Partial | readiness, counters, latency, per-case usage and cost | queue age, dead letters, tracing, logs, and alert definitions need implementation |
| Testability | Good | deterministic provider, HTTP mock transport, API workflow and recovery tests | PostgreSQL concurrency and OIDC cryptographic integration tests missing |
| Delivery | Good | pinned CI actions, migration round trip, coverage gate, non-root image, Compose | CI result depends on repository/account runner availability |
| Documentation | Strong | architecture, sequences, ADRs, threat/failure models, runbook, roadmap | production service-level and governance decisions intentionally unset |

## Evidence-backed strengths

### Control flow is deterministic around the model

The provider can return only the typed `ModelAnalysis` contract. The application independently
checks confidence and verifies that each cited quote exists in the exact retrieved policy version.
Every completed result enters `REVIEW_REQUIRED`; the provider cannot approve or reject a case.

### Recovery prevents stale writes

Analysis and notification finalization require the current state, fencing token, and active lease.
This is stronger than a status-only queue because an expired process cannot overwrite work claimed
after recovery.

### Review evidence is durable

The review record stores actor, rationale, decision, timestamp, and a hash of the analysis snapshot.
The final state, review record, audit event, and notification intent commit together.

### Claims remain calibrated

The deterministic retriever is described as lexical and inspectable rather than marketed as
semantic search. Cost is identified as an estimate, not invoice truth. No accuracy, scale,
availability, savings, certification, or production-use claim is made.

## Blocking gaps before production use

1. **Data governance:** classify documents, decide provider eligibility, encrypt data, enforce
   retention/deletion/legal hold, and implement tenant isolation.
2. **Evaluation:** define representative labeled and adversarial datasets, acceptance thresholds,
   regression policy, and prompt/model promotion gates.
3. **Identity and authorization:** integrate the actual identity provider, constrain role claims,
   add tenant/resource authorization, and test key rotation.
4. **Audit integrity:** export append-only evidence to separately controlled immutable storage.
5. **Operational readiness:** implement queue-age/dead-letter alerts, structured redacted logs,
   traces, dashboards, service objectives, and backup restore exercises.
6. **Provider resilience:** add explicit retry/circuit-breaker policy, budget limits, lease renewal,
   and graceful worker shutdown.
7. **Supply chain and deployment:** scan and sign images, generate an SBOM, use workload identity,
   inject managed secrets, and enforce network policy.

## Review questions for an implementation team

- What decision is the reviewer making, and what independent evidence must they inspect?
- Which data classifications may be sent to each configured model provider?
- How are policy and prompt versions promoted, rolled back, and linked to evaluation results?
- What is the safe response when retrieval returns no relevant policy?
- How is reviewer disagreement or correction represented without mutating evidence?
- What is the maximum acceptable queue and review age?
- Who can re-drive a dead-letter notification, and how is that action audited?
- How is a complete decision package exported and independently verified?

## Recommendation

Use this repository as architecture and implementation evidence for durable AI workflow boundaries.
For a real deployment, begin with evaluation and data governance rather than expanding model
features. Preserve mandatory review until domain-specific evidence supports a deliberately approved
change in risk posture.
