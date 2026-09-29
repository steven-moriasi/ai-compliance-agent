# ADR 005: Use effective-dated, section-qualified citations

- Status: Accepted
- Date: 2026-09-27

## Context

A quote found anywhere in a complete policy document is weak evidence when the analysis claims a
specific provision. Multiple versions can also exist, and a version outside the case date should
not become evidence merely because its wording ranks highly.

The repository needs a deterministic source identity that can be checked in code and presented to
a reviewer without claiming that lexical retrieval establishes legal meaning.

## Decision

Store ordered sections under each policy version. Each section has a stable reference, optional
heading, text, and position. Policies without explicit sections receive a `document` fallback
section to preserve existing behavior.

Filter active policy versions using a half-open effective window:

```text
effective_from <= case_date < effective_to
```

Missing bounds are open. Until the case model has an explicit applicability date, use the case
creation date.

Rank sections deterministically by lexical overlap with the submitted document. Require every
model citation to identify:

- policy ID;
- policy version;
- section reference;
- exact source quote.

Resolve the complete identity `(policy_id, policy_version, section_ref)` and verify the normalized
quote only against that section. Preserve the selected source identity in the analysis record for
human review.

## Consequences

- A quote from one section cannot validate a citation to another section in the same policy.
- Effective dating prevents expired or future versions from becoming retrieved evidence.
- Existing whole-document policies remain compatible through the fallback section.
- Source references are inspectable and deterministic.
- Manual section boundaries and effective dates can be wrong; source ingestion and activation need
  production provenance and approval controls.
- Lexical overlap can miss relevant sections, and exact quote presence does not prove correct
  interpretation.
- Case creation time is only a temporary applicability proxy.

## Alternatives considered

- **Validate against the whole policy document:** rejected because it permits incorrect section
  references to borrow text from another provision.
- **Require sections for every existing policy immediately:** rejected because it would break
  existing policies and migrations without improving their source text.
- **Use embeddings as the only retriever:** rejected because opaque similarity does not remove the
  need for effective dating, deterministic source identity, or exact citation validation.
- **Treat `effective_to` as inclusive:** rejected in favor of half-open windows that allow adjacent
  versions without sharing a boundary date.
