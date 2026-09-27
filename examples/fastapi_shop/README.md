# fastapi-shop

A minimal shop wired to dpdpkit-fastapi. It shows notice and consent, `require_consent`, rights requests,
erasure handlers, export collectors and the admin API.

```bash
pip install -e "../..[scheduler]"
uvicorn app:app --reload
```

Try it (the `X-User` header stands in for real authentication; `staff` is an admin):

```bash
curl localhost:8000/dpdp/notices/current?locale=hi
curl -X POST localhost:8000/dpdp/consents -H "X-User: u_42" -H "content-type: application/json" \
     -d '{"decisions":[{"purpose":"marketing","granted":true}]}'
curl -X POST localhost:8000/offers/send -H "X-User: u_42"          # 200
curl -X POST localhost:8000/dpdp/consents/marketing/withdraw -H "X-User: u_42"
curl -X POST localhost:8000/offers/send -H "X-User: u_42"          # 403 consent_required
curl localhost:8000/dpdp/admin/requests -H "X-User: staff"
```
