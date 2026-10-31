# Changelog

All code packages in the dpdpkit release train share one version number.

## Unreleased

### Added
- Durable webhooks (`WebhookOutbox`): events are queued in `dpdp_webhook_deliveries` (migration `0002`),
  sent by the scheduler with exponential backoff (10 attempts, about 4 hours) and kept as dead letters
  after that. `DPDPKit(webhooks=[...])` or `webhooks:` in dpdpkit.yaml (secrets via `secret_env`).
- CLI: `dpdpkit webhooks list|dead|send|retry`.
- `start_scheduler()` exports the ledger root hash daily (`root_export_hour`, `root_sinks`); `export_root_job()`.

### Changed
- Requires the dpdpkit-core release that adds `RootSink` (next in the train after 0.1.0a1).

## 0.1.0a1 - 2026-09-27

### Added
- `DPDPKit` with `install()`, `mount_admin()`, `require_consent()`, `start_scheduler()`, `migrate()`.
- `SqlAlchemyRepository` implementing the dpdpkit-core `Repository` protocol.
- Alembic migration `0001` and the `dpdpkit migrate` CLI subcommand.
- Router for the full REST contract; incident endpoints return 501 until v0.5.
- `ActivityMiddleware`; `examples/fastapi_shop`.
