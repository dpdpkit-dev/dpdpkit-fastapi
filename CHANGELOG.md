# Changelog

All code packages in the dpdpkit release train share one version number.

## Unreleased

### Added
- `GET /admin/consent/stats` (viewer role): grants, denials and withdrawals per purpose and notice version.
- Incident endpoints: `GET, POST /admin/incidents`, `GET /admin/incidents/{id}` and
  `GET /admin/incidents/{id}/report?kind=` (a Markdown draft; nothing is submitted to any authority). Incident
  and principal-notice storage in `dpdp_incidents` and `dpdp_principal_notices` (migration `0003`).
  `/guardian/verify/*` stay `501 not_implemented` until `dpdpkit.guardians` (CORE-25) ships.
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
