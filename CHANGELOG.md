# Changelog

All code packages in the dpdpkit release train share one version number.

## Unreleased

## 0.1.0a1 - 2026-09-27

### Added
- `DPDPKit` with `install()`, `mount_admin()`, `require_consent()`, `start_scheduler()`, `migrate()`.
- `SqlAlchemyRepository` implementing the dpdpkit-core `Repository` protocol.
- Alembic migration `0001` and the `dpdpkit migrate` CLI subcommand.
- Router for the full REST contract; incident endpoints return 501 until v0.5.
- `ActivityMiddleware`; `examples/fastapi_shop`.
