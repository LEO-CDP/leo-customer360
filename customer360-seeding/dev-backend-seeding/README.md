# Backend Demo Seeding

These scripts seed the PostgreSQL-backed Customer Identity Resolution demo:

- `init_sample_data.py` creates the tenant-scoped raw-profile fixture.
- `generate_fuzzy_match_demo.py` adds address and company variations for
  fuzzy-match verification.
- `seed_full_demo_data.py` enriches resolved profiles with CRM, relationship,
  persona, and behavioral-event fixtures.
- `seeding_utils.py` provides validated metadata models and reusable UUID,
  random, URL, event-partition, and vector helpers.
- `seed_demo_metadata.json` holds the full-demo catalogs: CRM entities,
  campaign fixtures, names, data sources, agents, domain settings, and persona
  archetypes. Credentials and environment overrides remain outside the file.
- `seeding_demo_contents.json` holds the validated recommendation catalog with
  seven realistic items for each supported domain.
- `seeding_content_items.py` loads and validates `seeding_demo_contents.json`
  before seeding `cdp_content_items`.
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

The content catalog can be seeded independently after the database schema and
demo tenant exist:

```bash
python customer360-seeding/dev-backend-seeding/seeding_content_items.py
```

`seed_full_demo_data.py` imports the same function, so the full demo flow and
the standalone content seeder use one catalog implementation.

## Full-demo seeder

`FullDemoSeeder` coordinates database stages, S3 event generation, source
statistics, and connection cleanup. It preserves the existing seed order and
commits once the stages complete. A failure rolls back the database and is
reported explicitly; S3 writes are external side effects and are not rolled
back by PostgreSQL.

Metadata is loaded relative to `seeding_utils.py`, not the working directory.
Missing files, unknown keys, or invalid catalog shapes fail before seeding.
The Docker image and deployment bundle copy the entire `dev-backend-seeding`
directory, including the JSON file.

The fixtures retain their existing synthetic VN/EU/US names. Master-profile
names and retail contact details may be synthetic plaintext; other inherited
PII remains hashed. Persona embeddings remain deterministic synthetic
vectors, not production model embeddings.

Run the focused tests from the repository root using an environment with the
backend seeding dependencies and local DAO installed:

```bash
python -m pytest customer360-seeding/test_seed_full_demo_data.py -q
```
