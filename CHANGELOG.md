# Changelog

All code packages in the dpdpkit release train share one version number.

## Unreleased

## 0.1.0a3 - 2026-11-03

### Added
- The wheel now includes the admin dashboard, served at `<prefix>/admin/ui/`. It was a placeholder page
  before. The version comes from `admin-bundle.version`; a source checkout keeps the placeholder.


## 0.1.0a2 - 2026-11-02

### Added
- `GET /admin/consent/stats` (viewer role): grants, denials and withdrawals per purpose and notice version.
- Incident endpoints: `GET, POST /admin/incidents`, `GET /admin/incidents/{id}` and
  `GET /admin/incidents/{id}/report?kind=` (a Markdown draft; nothing is submitted to any authority). Incident
  and principal-notice storage in `dpdp_incidents` and `dpdp_principal_notices` (migration `0003`).
- Guardian endpoints: `POST /guardian/verify/start` (202) and `POST /guardian/verify/callback` now run
  `dpdpkit.guardians` instead of returning 501. Verifiers are registered with
  `DPDPKit(guardian_verifiers=[...])`; an unconfigured method gives 422 and a confirmed adult gives 409.
  A principal with no age assertion is recorded as a self-declared minor needing a guardian. Age assertions and
  guardian verifications are stored in `dpdp_age_assertions` and `dpdp_guardian_verifications` (migration `0004`).
- Durable webhooks (`WebhookOutbox`): events are queued in `dpdp_webhook_deliveries` (migration `0002`),
  sent by the scheduler with exponential backoff (10 attempts, about 4 hours) and kept as dead letters
  after that. `DPDPKit(webhooks=[...])` or `webhooks:` in dpdpkit.yaml (secrets via `secret_env`).
- CLI: `dpdpkit webhooks list|dead|send|retry`.
- Async apps: `DPDPKit(engine=AsyncEngine)` or an async `db_url` (asyncpg, aiosqlite, aiomysql, asyncmy)
  connects with the matching sync driver; `await dpdp.run(fn, *args)` calls kit methods off the event loop.
- App sessions: `await dpdp.run_in_session(async_session, fn, *args)` and `with dpdp.bind(session):` run kit
  calls inside the app's own transaction, in savepoints, so they commit or roll back with the app's writes.
- Celery and Arq entry points (`dpdpkit.fastapi.tasks`): `register_celery(celery_app, dpdp)` adds the
  retention, ledger-verify, root-export and webhook-delivery tasks plus a beat schedule;
  `arq_functions(dpdp)` and `arq_cron_jobs(dpdp)` do the same for Arq. Extras: `celery`, `arq`.
- `start_scheduler()` exports the ledger root hash daily (`root_export_hour`, `root_sinks`); `export_root_job()`.

### Changed
- SQLite: dpdpkit's engine waits for locks (`busy_timeout` 30 s), uses WAL for file databases and starts
  transactions with `BEGIN IMMEDIATE`, so concurrent writers no longer fail with "database is locked".
- Requires the dpdpkit-core release that adds `RootSink` (next in the train after 0.1.0a1).

## 0.1.0a1 - 2026-09-27

### Added
- `DPDPKit` with `install()`, `mount_admin()`, `require_consent()`, `start_scheduler()`, `migrate()`.
- `SqlAlchemyRepository` implementing the dpdpkit-core `Repository` protocol.
- Alembic migration `0001` and the `dpdpkit migrate` CLI subcommand.
- Router for the full REST contract; incident endpoints return 501 until v0.5.
- `ActivityMiddleware`; `examples/fastapi_shop`.
