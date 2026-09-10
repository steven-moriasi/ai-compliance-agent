## Summary

<!-- What changed and why? -->

## Risk and failure behavior

<!-- What can fail, and how does the system recover or fail closed? -->

## Verification

- [ ] Ruff
- [ ] mypy
- [ ] affected tests
- [ ] full test suite with coverage gate
- [ ] migration round trip, if persistence changed
- [ ] container build or Compose validation, if runtime packaging changed

## AI and security review

- [ ] Model and document data remain untrusted
- [ ] Human review cannot be bypassed
- [ ] Citation and policy-version provenance are preserved
- [ ] No credentials, sensitive fixtures, or unsupported claims were added
- [ ] Architecture, threat model, runbook, or ADRs were updated where needed
