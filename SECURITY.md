# Security policy

## Reporting

Do not open a public issue for a suspected vulnerability or include sensitive documents,
credentials, tokens, provider responses, or exploit details in public discussion.

Use GitHub's private vulnerability reporting or repository Security Advisory flow. Include:

- affected revision and component;
- impact and required preconditions;
- minimal reproduction using synthetic data;
- recommended remediation if known.

## Supported versions

This reference implementation is maintained on the default branch only. It has not been certified
for production use or for any regulatory framework.

## Security boundaries

Review the threat model before deployment. A deployment owner is responsible for TLS, network
policy, secret management, database hardening, backups, identity-provider configuration, data
classification, model-provider agreements, retention, and immutable audit export.

Development-header authentication and example credentials are local-only. Production deployments
must use OIDC mode and managed secrets.
