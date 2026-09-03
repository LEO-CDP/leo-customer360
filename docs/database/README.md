# Customer 360 Database Tools

Read-only database checks and local backup guidance for the Customer 360
platform.

## Database Audit

The [check-db-data.sql](check-db-data.sql) script audits the selected tenant
and the core Customer 360 tables. It is designed to run directly in the
pgAdmin Query Tool.

### Run In pgAdmin

1. Connect pgAdmin to the target `customer360` database.
2. Open [check-db-data.sql](check-db-data.sql) in Query Tool.
3. Change the tenant UUID near the top of the file:

	 ```sql
	 SET app.tenant_id = '11111111-1111-1111-1111-111111111111';
	 ```

4. Execute the complete file with `ON_ERROR_STOP` enabled if your pgAdmin
	 version provides that option.
5. Review the result grids, especially the final consolidated issue summary.

The script is read-only. It uses `app.tenant_id` for tenant-scoped queries so
the checks follow the same tenant context and row-level security rules as the
API. The tenant UUID must exist in `customer360.sys_tenant`; an empty tenant
result means the selected UUID is incorrect for that database.

### Audit Coverage

The report includes:

- Tenant, configured domains, database session, and row-level security status
- Row counts for system, profile, identity, event, persona, CRM, content, and
	segmentation tables
- Master profile lifecycle, domain distribution, identity completeness, and
	missing score fields
- Invalid domains and cross-tenant profile/domain relationships
- Raw profile ingestion status, stale records, CIR links, identity indexes,
	unresolved events, and duplicate event deduplication keys
- Transaction linkage, transaction totals, customer contacts, and tenant
	consistency
- Segment domains, names, descriptions, rules, stored member counts, and
	calculated member counts
- Persona assignments, duplicate active personas, embeddings, current persona
	pointers, and archetype member-count consistency
- CRM campaign relationships, opportunity/account links, contact/account links,
	and profile relationship integrity
- Unvalidated PostgreSQL constraints

### Summary Status

The final result grid reports one row per check:

| Status | Meaning |
| --- | --- |
| `PASS` | The check is healthy or has no issue rows. |
| `WARN` | Data exists that needs review, but the script completed successfully. |
| `FAIL` | The selected tenant is missing or a critical setup check failed. |

Warnings are data-quality signals, not SQL errors. For example, unresolved
events may be expected immediately after ingestion, while a growing number of
stale raw profiles may indicate that CIR processing is not keeping up.

## UAT Backup

Use [dev-db-backup.sh](dev-db-backup.sh) to create a plain UTF-8 SQL backup from
the running local PostgreSQL container:

```bash
./docs/database/dev-db-backup.sh \
	--env-file .env \
	--output backups/customer360-uat.sql \
	--data-mode inserts
```

The default backup is suitable for uploading through pgAdmin Query Tool:

- Dumps only the `customer360` application schema
- Uses normal `INSERT` statements instead of `COPY FROM stdin`
- Sets `client_encoding` to UTF-8
- Excludes ownership, privilege, and comment statements
- Does not attempt to create extensions such as `postgis_topology`
- Writes a matching `.sha256` checksum file

Before importing into UAT, a database administrator must install the required
PostgreSQL extensions in the target database. The import role must also have
permission to create the `customer360` schema and application objects, or the
schema must be prepared by an administrator first.

In pgAdmin Query Tool, open [backups/customer360-uat.sql](../../backups/customer360-uat.sql)
and execute it against a new or empty UAT database. Do not use the pgAdmin
Restore dialog for this plain SQL file.

For a direct PostgreSQL restore with `psql`, `--data-mode copy` is smaller and
faster, but it is not recommended for web-based SQL upload tools.