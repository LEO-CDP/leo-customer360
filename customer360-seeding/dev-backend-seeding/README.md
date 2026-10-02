# Backend Demo Seeding

These scripts seed the PostgreSQL-backed Customer Identity Resolution demo:

- `init_sample_data.py` creates the tenant-scoped raw-profile fixture.
- `generate_fuzzy_match_demo.py` adds address and company variations for
  fuzzy-match verification.
- `seed_full_demo_data.py` enriches resolved profiles with CRM, relationship,
  persona, and behavioral-event fixtures.
- `utils.py` contains the shared PII hashing helper used before database
  insertion.

The scripts require the backend seed dependencies and an installed local
`leo-customer360-dao` package. The normal CIR launcher installs those
dependencies and invokes the scripts from the repository root:

```bash
customer360-backend/identity_resolution/run-demo.sh
```

They are also included in the backend Docker image at
`/opt/customer360-seeding` for the one-shot compose, Kubernetes, and deployment
seed jobs.
