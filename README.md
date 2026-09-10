# AI Compliance Agent

A portfolio/reference implementation of a human-reviewed compliance analysis workflow. It applies an LLM behind deterministic retrieval, validation, abstention, review, and audit boundaries rather than presenting a chatbot as an enterprise control.

## Intended flow

```text
document -> content hash -> policy retrieval -> structured model result
         -> deterministic validation -> human review -> decision record
```

The project is under incremental development. It does not claim to provide legal advice, replace compliance professionals, or reproduce a client system.
