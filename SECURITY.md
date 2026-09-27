# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Email **security@dpdpkit.dev** (placeholder until the domain is registered — see task SETUP-13) or use
GitHub's private vulnerability reporting on this repository ("Security" tab → "Report a vulnerability").

Include the affected package and version, a description, and steps to reproduce. We aim to acknowledge
reports within 3 working days and to ship a fix or mitigation within 30 days for confirmed issues.

## Supported versions

Only the latest minor release line receives security fixes before v1.0.

## Scope notes

dpdpkit stores consent records, rights requests and audit logs. Reports about ledger tampering that is
not detected by `verify()`, cross-tenant data access, or erasure that runs without its recorded warning
are treated as high severity.
