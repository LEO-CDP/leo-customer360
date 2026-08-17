# vDB PostgreSQL: cannot provision 10000‑IOPS standalone in the account's enabled zone

- **Date:** 2026-08-17
- **Status:** OPEN — blocked on GreenNode support (AZ enablement)
- **Severity:** Medium (deployable at 3200 IOPS today; 10000 IOPS blocked)
- **Component:** `deployments/postgres` (Terraform, provider `vngcloud/vngcloud` v1.3.19)
- **Cloud:** GreenNode / VNG Cloud vDB, project `pro-8986f5c6-02ca-4647-be9a-4070bb100559`

## Summary

We need a **10000‑IOPS single‑node (standalone) PostgreSQL** instance. That is **not
possible in the only zone this account can currently use**, because the zone that is
enabled for networking does not offer the 10000‑IOPS standalone volume, and the zone that
offers it is disabled for networking.

To unblock 10000 IOPS on a single node, **GreenNode must enable Availability Zone
`HCM03-1A` for vServer** (VPC/subnet creation) on the project above.

## Impact

- **Today:** we can deploy standalone PostgreSQL only in `HCM03-1C`, where the storage
  tops out at **3200 IOPS**. The current overlays are set to this (working) config.
- **Blocked:** the 10000‑IOPS requirement for a standalone instance.

## Root cause

A managed vDB instance must attach to a **vServer subnet**, and a subnet can only be
created in a **vServer‑enabled availability zone**. The vDB **volume catalog differs per
zone**, and so does whether standalone offers a 10000‑IOPS tier.

For this account:

| Availability zone | vServer enabled? | Standalone volume tiers | 10000 IOPS standalone? |
| --- | --- | --- | --- |
| `HCM03-1A` | **No** (disabled — "Contact to enable") | `Gen2-NVMe2-IOPS10000`, … | ✅ yes |
| `HCM03-1B` | No (disabled) | — | — |
| `HCM03-1C` | **Yes** (enabled + default) | `ssd-iops{200,400,800,1000,1200,1600,3000,3200}-HCM03-1C` | ❌ no (max 3200) |

- The subnet can only go in **`HCM03-1C`** (the only enabled AZ).
- `HCM03-1C` standalone maxes at **3200 IOPS**.
- `HCM03-1A` standalone offers **10000 IOPS** (`Gen2-NVMe2-IOPS10000`) but its vServer is
  disabled, so the subnet (and therefore the DB) cannot be placed there.
- 10000 IOPS **does** exist in `HCM03-1C` — but only for the **cluster** topology
  (`vngcloud_vdb_postgresql_cluster`, volume `SSD-IOPS10000`), not standalone.

## Evidence

Errors seen during `terraform apply` (HCM03-1A attempt):
```
Error: request fail ... Status Code: 404, {"message":"Cannot get zone with id HCM03-1A"}
  with vngcloud_vserver_subnet.this[0]
```

Zones for the project (`GET /vserver/vserver-gateway/v1/{project}/zones`):
```
uuid=HCM03-1A  name=HCM-1A  enabled=False default=False
uuid=HCM03-1B  name=HCM-1B  enabled=False default=False
uuid=HCM03-1C  name=HCM-1C  enabled=True  default=True
uuid=HCM03-BKK-01 name=HCM-BKK-1A enabled=False default=False
```

Standalone volume tiers in HCM03-1C
(`GET /vdb-gateway.vngcloud.vn/vdb-relational/v1/database-instances/volume/types?zoneId=HCM03-1C`):
```
ssd-iops200-HCM03-1C   ssd-iops400-HCM03-1C   ssd-iops800-HCM03-1C
ssd-iops1000-HCM03-1C  ssd-iops1200-HCM03-1C  ssd-iops1600-HCM03-1C
ssd-iops3000-HCM03-1C  ssd-iops3200-HCM03-1C          <-- max is 3200
```

Catalog naming differs by zone: HCM03-1A uses `db.s2-general-*` packages +
`Gen2-NVMe2-IOPS10000` volumes; HCM03-1C uses `db.s-general-*` packages +
`ssd-iops<N>-HCM03-1C` volumes.

## Options

| # | Option | IOPS | Trade-offs |
| --- | --- | --- | --- |
| A | **Enable `HCM03-1A` for vServer** (support ticket), run standalone there | 10000 ✅ | Needs GreenNode to enable the AZ; single node; the config we want |
| B | Switch to vDB **cluster** in HCM03-1C (`vngcloud_vdb_postgresql_cluster`, `SSD-IOPS10000`) | 10000 ✅ | HA (1 writer + N readers); notably higher cost; backups via VNG Backup Center (`backup_auto` is a no-op on clusters) |
| C | Accept **3200 IOPS** standalone in HCM03-1C (current) | 3200 | Cheapest/simplest; below requirement |

## Current decision

Deploy at **3200 IOPS** in `HCM03-1C` now (Option C, overlays set to this), and pursue
**Option A** to reach 10000 IOPS.

## Action items

- [ ] File a GreenNode support ticket (`helpdesk.greennode.ai`): **enable Availability
      Zone `HCM03-1A` for vServer/VPC (subnet creation)** on project
      `pro-8986f5c6-02ca-4647-be9a-4070bb100559`. Also request **vDB standalone** in 1A if
      separately gated.
- [ ] Once enabled, flip the overlays back to the HCM03-1A values and apply:
      `zone_id = "HCM03-1A"`, `package_name = "db.s2-general-8x16"`,
      `volume_type = "Gen2-NVMe2-IOPS10000"`.
- [ ] Re-evaluate Option B (cluster) if HA is also wanted.

## Notes

- The Terraform config wires optional VPC+subnet creation (`create_network = true`) and
  drives everything off `var.zone_id`, so switching zones is a 3‑value change in the
  overlay (`zone_id`, `package_name`, `volume_type`).
- The vDB catalog endpoints are undocumented; they were recovered from the provider binary
  (`grep -a` the `terraform-provider-vngcloud*.exe` for `/vdb-relational/...` paths) and
  queried with a Bearer token + `portal-user-id` header.
