# dpdpkit-fastapi

FastAPI adapter for [dpdpkit](https://github.com/dpdpkit-dev). It adds consent, versioned notices, rights
requests and retention to your FastAPI app, with tables in your own database. This is the reference
implementation of the dpdpkit REST contract.

> **Disclaimer.** dpdpkit is software that helps you implement obligations under India's Digital Personal
> Data Protection Act, 2023 and the DPDP Rules, 2025. It does not provide legal advice and does not
> guarantee compliance. Decisions about notices, purposes, retention and incident reporting must be made
> by you or your counsel.

## Install

```bash
pip install "dpdpkit[fastapi]"                          # or: pip install dpdpkit-fastapi
pip install "dpdpkit-fastapi[scheduler,postgres]"       # APScheduler + psycopg
```

## Quickstart

```bash
dpdpkit init            # writes dpdpkit.yaml: purposes, notice text, contact details
```

```python
from fastapi import Depends, FastAPI
from dpdpkit.fastapi import DPDPKit, require_consent

app = FastAPI()
dpdp = DPDPKit(
    db_url=settings.DATABASE_URL,
    config="dpdpkit.yaml",  # policy pack, purposes, notice
    principal_resolver=lambda request: request.state.user.id,  # stable id, no personal data
    admin_guard=lambda request: "admin" if request.state.user.is_staff else None,
    signing_key=settings.DPDPKIT_SIGNING_KEY,  # signs consent receipts
    contact_resolver=lookup_contact,  # principal -> Contact(email=..., phone=...)
    notifier=SmtpNotifier("smtp.example.in", sender="privacy@example.in"),
)
dpdp.install(app, prefix="/dpdp")  # router + error handlers + activity middleware + admin


@dpdp.kit.retention.register_handler
def erase(principal: str, purposes: list[str]) -> None: ...  # delete or anonymise your own rows


@app.post("/offers/send", dependencies=[Depends(require_consent("marketing"))])
async def send_offer(): ...
```

```bash
dpdpkit migrate --db-url "$DATABASE_URL"        # Alembic migrations shipped in the package
```

Run the scheduler in one process (or call `dpdpkit retention run` from cron):

```python
dpdp.start_scheduler(retention_minutes=15)  # retention run + daily ledger verification
```

## What you get

| Component | Detail |
| --- | --- |
| `SqlAlchemyRepository` | SQLAlchemy 2.0; Postgres, MySQL, SQLite; microsecond timestamps so ledger hashes survive round trips |
| Migrations | Alembic, forward-only, shipped in the package (`dpdpkit migrate`) |
| Router | Full REST contract from [`dpdpkit-spec`](https://github.com/dpdpkit-dev/dpdpkit-spec) |
| `require_consent()` | Dependency returning 403 `consent_required` with the purpose |
| Roles | `admin_guard` returns `viewer`, `handler` or `admin` |
| Scheduler | APScheduler (`[scheduler]` extra); Celery and Arq entry points in v0.5 |
| Activity middleware | Records activity, restarting inactivity clocks and cancelling warned erasures |
| Admin mount | Serves the admin dashboard bundle (placeholder page until v0.4) |

## Example

[`examples/fastapi_shop`](examples/fastapi_shop) is a small shop with consent, requests and erasure
handlers. It passes the conformance suite:

```bash
cd examples/fastapi_shop
uvicorn app:app --reload                                  # http://localhost:8000/docs
DPDPKIT_CONFORMANCE=1 DPDPKIT_ASGI_APP=app:app dpdpkit-conformance
```

## Licence

Apache-2.0.
