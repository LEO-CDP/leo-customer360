# vStorage: create-project API charged the wallet on a failed create; wrong region (HCM03) was the cause

- **Date:** 2026-08-17
- **Status:** OPEN — refund needed for 6 orphaned charges; region corrected to HCM04 in the module
- **Severity:** HIGH (real money charged: ~3,162,000 VND for projects that never provisioned)
- **Component:** `deployments/storage` (Terraform buckets + a since-removed `scripts/create-project.sh` helper)
- **Cloud:** VNG Cloud / GreenNode vStorage Object Storage

## ⚠️ Billing impact (READ FIRST)

`POST /api/v1/projects` returned `code 114` ("Could not send order request: Error
occurred when creating project") on every call — **but the billing order was still
charged each time.** Six charges resulted, and **no project was provisioned**
(confirmed: `GET /projects` returns `datas: null` on the hcm03, hcm04, AND han02
API hosts):

| Time (17/08/2026) | Item | Amount (VND) | Source |
| --- | --- | --- | --- |
| 22:27:14 | vStorage-Gold 30d | 1,024,000 | first script run (quota 1024) |
| 22:33:45 | vStorage-Gold 30d | 1,024,000 | script run |
| 22:34:38 | vStorage-Gold 30d | 1,024,000 | script run |
| 22:36:52 | vStorage-Gold 30d | 30,000 | probe (quota 30) |
| 22:36:54 | vStorage-Gold 30d | 30,000 | probe (quota 30) |
| 22:36:56 | vStorage-Gold 30d | 30,000 | probe (quota 30) |
| **Total** | | **3,162,000** | |

**Action: request a refund** from VNG/GreenNode support for these 6 failed
`vStorage-Gold` orders (charged, never provisioned). There is nothing to *delete*
— the projects do not exist in any region.

## Root cause: HCM03 is not an object-storage region for this account

`GET /api/v1/regions` returns only **HCM04** and **HAN02**. HCM03 (used by
`deployments/postgres` for compute/vDB) has **no** object storage. The module and
the removed helper defaulted to `hcm03`, so every create hit an invalid region →
`code 114`. **The billing order was placed before/independently of region
validation, so it charged despite the resource failing.** The module default is
now **HCM04** (`variables.tf`, overlays).

## Summary

A vStorage **project** must exist before any bucket can be created, and it is a
**paid, billed** resource (you pick a quota/package and go through *Checkout*).
The vStorage REST API exposes `POST /api/v1/projects`, but for this account every
create attempt fails at the **billing-order** step with:

```
HTTP 200
{"errorMsg":"Could not send order request: Error occurred when creating project","code":114,"success":false}
```

The failure is **independent of the request body** (proven below), so it is an
**account / billing-service condition, not a payload or script bug**. The project
must be created in the **console** (`https://vstorage.console.vngcloud.vn` →
Create a Project → Checkout), where payment is attached.

## Impact

- **Blocked:** scripting the project creation (Infrastructure-as-Code end-to-end).
- **Not blocked:** creating the project by hand in the console, then managing
  **buckets** with Terraform (`deployments/storage`) via an S3 key scoped to that
  project. That path is fully working.

## Why it can't be Terraform / why it was a script

- The `vngcloud/vngcloud` Terraform provider does **not** expose vStorage at all
  (only vServer, vDB, vLB, vKS) — so buckets already use the `hashicorp/aws`
  provider against the S3 endpoint, and a project can't be a TF resource.
- A vStorage project is **billed** and holds data, so even via API it was kept out
  of Terraform state (a bootstrap script), so `terraform destroy` could never wipe
  a paid project. That script is now removed because its create step can't succeed
  for this account (see below).

## Evidence

Auth works and the account is reachable — `GET /api/v1/projects` returns success
with an **empty** list (note the list wrapper is `datas`, not `data`):
```
GET https://hcm03-api.vstorage.vngcloud.vn/api/v1/projects
{"errorMsg":null,"code":200,"success":true,"datas":null}
```

Create fails identically for **every** payload variation (same `code 114`):

| Payload (projectType, quotaInGBytes, isPoc, enableAutoRenew) | Result |
| --- | --- |
| Gold, 1024, false, false | `code 114` Could not send order request |
| Gold, 30, false, false | `code 114` Could not send order request |
| Gold, 30, **true** (POC), false | `code 114` Could not send order request |
| Gold, 30, false, **true** | `code 114` Could not send order request |
| Instant Archive, 30, false, false (+`archivePeriod`) | `code -1` Unknown error |

Because `code 114` is identical across paid/POC, min/max quota, and auto-renew
on/off, the request body is not the problem — the internal order placement is.

## Facts learned about the vStorage project API (for whoever retries)

- **Endpoint:** `POST https://hcm03-api.vstorage.vngcloud.vn/api/v1/projects`
  (control plane is `<region>-api.vstorage.vngcloud.vn`, distinct from the S3 data
  plane `<region>.vstorage.vngcloud.vn`).
- **Auth:** vIAM bearer token — OAuth2 client-credentials with **HTTP Basic auth**
  (`Authorization: Basic base64(clientId:clientSecret)`) and JSON body
  `{"grant_type":"client_credentials"}` against
  `https://iamapis.vngcloud.vn/accounts-api/v2/auth/token`. Form-encoded creds → 400.
- **Create body** (`VosProjectBillingCreatingReq`): required `projectName`,
  `projectType` (`"Gold"` = hot | `"Instant Archive"` = cold), `quotaInGBytes`;
  optional `enableAutoRenew`, `isPoc`, `archivePeriod`. No `region` field — region
  is fixed by the host.
- **Quota limits:** Gold `quotaInGBytes` must be **30 – 2,000,000 GB**
  (below 30 → `code 112` "Quota must from 30GB to 2000000GB").
- **API returns HTTP 200 even on failure** — success/failure is in the body's
  `success` flag + `errorMsg`; never trust the status code alone (don't use `curl -f`).

## Root cause (most likely)

The vStorage backend forwards project creation to a separate **order/billing**
service, and that call fails (`code 114`). For this account, one of:

- no **payment method** attached (paid) / no funded **POC wallet** (POC) — the
  console attaches this at *Checkout*;
- the **vIAM service account** lacks **billing/order** permission (its IAM policy
  likely grants vStorage read/write, not order placement);
- this account cannot place vStorage orders programmatically at all.

## Current decision

Create the vStorage project **manually in the console** (region HCM03, Gold,
≥30 GB, Checkout). Then use `deployments/storage` (S3 key + Terraform) to manage
buckets inside it. The non-working `create-project.sh` helper was **removed** from
`deployments/storage` to avoid a dead path.

## Action items

- [ ] **Request a refund** for the 6 failed `vStorage-Gold` orders above (charged,
      never provisioned) — VNG/GreenNode support, referencing the timestamps.
- [ ] Create the project in the console: `https://vstorage.console.vngcloud.vn` →
      **Create a Project** → region **HCM04** (or HAN02) → **Gold**, quota ≥ 30 GB → **Checkout**.
- [ ] In that project, create an **S3 key** (IAM → Service account → vStorage
      credentials → Create a S3 key) and put it in `deployments/storage/terraform.tfvars`.
- [ ] `cd deployments/storage && ./deploy.sh uat plan` to provision buckets.
- [ ] (Optional, to unblock API creation) Ask VNG support / account admin to attach
      a payment method (or fund a POC wallet) and grant the vIAM service account
      billing/order permission; then project creation via
      `POST /api/v1/projects` should return the created `projectId`.

## Notes

- Confirmed against the live API on 2026-08-17 (auth OK, list OK, create → `code 114`).
- Bucket management (`deployments/storage`) is unaffected and ready; it only needs
  the project + an S3 key to exist.
