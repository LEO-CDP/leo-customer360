# Customer 360 — vServer → VKS Cost Analysis

> **Status:** research / decision-support · **Date:** 2026-08-26 · **Scope:** UAT + PROD
> **Companion:** [`vks-migration-technical-analysis.md`](./vks-migration-technical-analysis.md)
> **FX used:** ≈ **26,000 VND = 1 USD** (external market level, Aug 2026 — VNG prices only in VND).

## 0. Sources & confidence

VNG's pricing **calculator** (`calculator.console.greennode.ai`) is **decommissioned** (NXDOMAIN),
and the docs deliberately carry no per-resource numbers. **However**, the real, current
**Pay-As-You-Go list prices are published** as server-rendered CMS data on the VNG product pages
(`vngcloud.vn/vi/product/{vserver,vdb,vstorage}`). So the compute, managed-DB and MemStore numbers
below are **published** (VAT-inclusive monthly list prices); only **block-storage per-GB** and the
**load-balancer package** remain **estimated** (calculator-only). Every figure is tagged.

| Bucket | Confidence |
|---|---|
| vServer compute flavors (= VKS worker-node prices) | ✅ **published** (product page) |
| Managed PostgreSQL / MemStore instance prices | ✅ **published** (product page) |
| VKS control plane = free | ✅ **published** (docs) |
| Object storage (vStorage) per GB | ✅ published (usage; identical in both models) |
| **Block volume / PVC SSD per GB** | ⚠️ **estimated** ~2,000 VND/GB/mo (from vDB storage anchor) |
| **Load balancer (`NLB_Small`) per month** | ⚠️ **estimated** ~300,000 VND/mo (market placeholder — confirm in console) |

> The two estimated lines (PVC, NLB) are **identical in both deployment models**, so they largely
> **cancel out in the delta** — the comparison is robust even though those two rates aren't published.

---

## 1. Cost model & the one insight that decides it

```
Total/mo =  Σ(worker/VM vCPU+RAM · flavor price)     ← compute
          + Σ(disk + PVC GB · block price)           ← storage
          + Σ(load balancers · NLB price)            ← ingress
          + managed PostgreSQL + managed MemStore    ← unchanged by the migration
          + object storage + egress                  ← ≈ unchanged
          + VKS control plane ....................... = 0 (free)
```

**The decisive fact:** VNG vServer pricing is **perfectly linear** — a fixed price per
**(1 vCPU + 2 GB) block**, and **VKS worker nodes are billed at those same flavor prices**:

- `s-general` (Intel gen-1): **236,500 VND** per (1 vCPU + 2 GB)/mo
- `s2-general` (Intel gen-2): **283,800 VND** per (1 vCPU + 2 GB)/mo (2x4 = 2×, 4x8 = 4×, 8x16 = 8×)

Because it's linear and the node price is the same VM-or-Kubernetes, **compute cost depends only on
the *total* vCPU/RAM you provision — not on how many boxes you slice it into.** Consequences:
- At **equal capacity**, VKS compute cost = vServer compute cost (no Kubernetes premium; control plane is free).
- VKS gets **cheaper** only when it lets you provision **less** total vCPU/RAM — via **bin-packing**
  (one shared pool instead of per-service boxes sized for isolated peaks) and **autoscaling** (pay for
  peak only when it happens).
- The **managed PostgreSQL + MemStore** dominate the bill and are **unchanged**, so they cancel in the delta.

---

## 2. Published rate card (VND/month)

| Resource | Spec | VND/month | Basis |
|---|---|---|---|
| vServer `s-general-1x2` | 1 vCPU / 2 GB | **236,500** | published |
| vServer `s2-general-2x4` | 2 / 4 | **567,600** | published |
| vServer `s2-general-4x8` | 4 / 8 | **1,135,200** | published |
| vServer `s2-general-8x16` | 8 / 16 | **2,270,400** | published |
| Managed PostgreSQL `db.v1.small2x4.b100`¹ | 2 / 4 · 100 GB incl. | **840,000** | published |
| Managed PostgreSQL `db.v1.medium8x16.b100`¹ | 8 / 16 · 100 GB incl. | **3,360,000** | published |
| Managed MemStore `db.v1.small2x4.b100`¹ | 2 / 4 · 100 GB incl. | **840,000** | published |
| Block volume / PVC (SSD) | per GB-month | ~2,000 | ⚠️ estimated |
| Load balancer (`NLB_Small`) | per LB-month | ~300,000 | ⚠️ estimated |
| Object storage (vStorage, Gold/High-Perf) | per GB-month | 1,000 / 1,600 | published (usage) |
| **VKS control plane** | per cluster | **0 (free)** | published |

¹ The old `db.s-general-*` names are retired; current SKUs are `db.v1.*` (General) / `db.v2.*` (High-Perf),
`.b100` = 100 GB bundled storage. Extra DB storage ≈ 2,000 VND/GB/mo. A **Saving Plan** (committed 1–3 yr)
is cheaper than these PAYG list prices for steady prod — worth pricing separately.

---

## 3. Estimated monthly cost — vServer vs VKS

Assumptions: root/node disks and PVCs billed at ~2,000 VND/GB/mo; one `NLB_Small` per env in both
models; managed PostgreSQL + MemStore identical in both models; object-storage usage identical (excluded
from the compute comparison). UAT keeps Redis in-cluster; PROD keeps managed MemStore.

### 3.1 UAT

| Line | vServer (current) | VKS |
|---|---|---|
| Compute | 3 × `s-general-1x2` = **709,500** | 1 × `s2-general-4x8` = **1,135,200** |
| Node / root disks | 3 × 20 GB ≈ 120,000 | 1 × 50 GB ≈ 100,000 |
| PVCs (Jaeger / pgAdmin / Redis) | — (on-box volumes) | ~20 GB ≈ 40,000 |
| Redis | container on api box = 0 | in-cluster (PVC above) = 0 |
| Managed PostgreSQL (2×4) | 840,000 | 840,000 |
| Load balancer (`NLB_Small`) | ~300,000 | ~300,000 |
| VKS control plane | — | 0 |
| **Total / month** | **≈ 1,969,500 VND** (~$76) | **≈ 2,415,200 VND** (~$93) |

**UAT delta: VKS ≈ +445,700 VND/mo (~+23%, ~+$17).** The increase buys real capacity (4 vCPU/8 GB vs
today's *oversubscribed* 3 vCPU/6 GB single box), plus per-pod limits, HPA and self-healing. UAT is
small either way; a 2 × `s2-general-2x4` layout (node-level HA) costs the same compute + one extra
node disk (≈ +100,000). Redis stays in-cluster to avoid the 840,000 managed-MemStore line.

### 3.2 PROD

| Line | vServer (current, 4 boxes) | vServer (design-complete, 6 boxes) | VKS — like-for-like (3 × 4x8) | VKS — bin-packed (2 × 4x8 + autoscale) |
|---|---|---|---|---|
| Compute | 4x8+2x4+2x4+4x8 = **3,405,600** | + tracking 2x4 + backend 2x4 = **4,540,800** | 3 × `s2-general-4x8` = **3,405,600** | 2 × `s2-general-4x8` = **2,270,400** |
| Node / root disks | 4 × 50 GB ≈ 400,000 | 6 × 50 GB ≈ 600,000 | 3 × 50 GB ≈ 300,000 | 2 × 50 GB ≈ 200,000 |
| PVCs (Jaeger / pgAdmin) | — | — | ~25 GB ≈ 50,000 | ~25 GB ≈ 50,000 |
| Managed PostgreSQL (8×16, 250 GB)² | 3,660,000 | 3,660,000 | 3,660,000 | 3,660,000 |
| Managed MemStore (2×4) | 840,000 | 840,000 | 840,000 | 840,000 |
| Load balancer (`NLB_Small`) | ~300,000 | ~300,000 | ~300,000 | ~300,000 |
| VKS control plane | — | — | 0 | 0 |
| **Total / month** | **≈ 8,605,600 VND** (~$331) | **≈ 9,940,800 VND** (~$382) | **≈ 8,555,600 VND** (~$329) | **≈ 7,320,400 VND** (~$282) |

² `db.v1.medium8x16.b100` = 3,360,000 (100 GB incl.) + 150 GB extra × 2,000 = 300,000 → 3,660,000.

**PROD deltas (vs current 4-box, ≈ 8,605,600):**
- **VKS like-for-like** (same 12 vCPU / 24 GB, one autoscaling pool): **≈ −50,000 VND/mo (≈ break-even)** — proof of the linear-pricing point.
- **VKS bin-packed** (8 vCPU / 16 GB baseline, autoscale to 4 nodes for ads bursts): **≈ −1,285,200 VND/mo (~−15%, ~−$49)**.
- **vs the design-complete 6-box target** (≈ 9,940,800): bin-packed VKS saves **≈ −2,620,400 VND/mo (~−26%)** and still covers tracking + backend as pods (no extra boxes to buy).

> Managed PostgreSQL + MemStore (**≈ 4,500,000 VND/mo**) are **>50% of the PROD bill** and are
> **unchanged** by the migration. The only movable money is compute + disks + LB (≈ 4.1M), which is
> exactly where VKS bin-packing/autoscale wins.

---

## 4. Footprint delta (price-free, exact)

| | UAT now | UAT VKS | PROD now (prov.) | PROD now (design) | PROD VKS (bin-packed) |
|---|---|---|---|---|---|
| Worker vCPU | 3 (oversub.) | 4 | 12 | ~16 | 8 (autoscale →16) |
| Worker RAM | 6 GB (oversub.) | 8 GB | 24 GB | ~32 GB | 16 GB (→32) |
| Load balancers | 1 | 1 | 1 | 1 | 1 |
| Managed PG / MemStore | same | same | same | same | same |
| Control-plane fee | — | 0 | — | — | 0 |

---

## 5. TCO — the non-price factor

Infra spend is ≈ flat (UAT slightly up, PROD flat-to-cheaper). The migration pays off in **operations**:
deploys become declarative (no SSH `deploy-all.sh` pipeline), scaling is HPA/autoscaler (pay for peak
only when it happens), reliability improves (pod reschedule + node auto-repair), networking simplifies
(Service DNS + NetworkPolicy replace hand-maintained cross-box security groups), TLS/routing is one
mechanism (Ingress + cert-manager replace Caddy + the cutover runbook), and ops loses a tool (kubectl
replaces Portainer + agents). See the technical doc §7.

**One-time migration cost:** engineering to author manifests/Helm + platform add-ons + cutover, plus a
brief parallel run during cutover. No new licensing (VKS control plane free; NGINX/cert-manager/
oauth2-proxy are OSS). `data-tracking-api` is already CI-built (prerequisite done).

---

## 6. Verdict & recommendation

- **UAT:** VKS costs **~+446k VND/mo (~+$17)** — a rounding error that buys real capacity + HPA + self-healing over today's oversubscribed 1-box setup. **Migrate** (low risk, first).
- **PROD:** VKS is **break-even at equal capacity** and **~1.3M VND/mo cheaper (~−15%) bin-packed**, rising to **~2.6M/mo (~−26%)** vs the design-complete 6-box target — while the free control plane means no Kubernetes tax. **Migrate** after UAT.
- **Biggest lever is not the platform:** managed PostgreSQL + MemStore are >50% of PROD spend and unchanged. To cut the bill materially, right-size the DB tier and consider a **Saving Plan** (committed) for steady prod compute + DB — both orthogonal to the VKS decision.

### Confirm before committing budget
1. **`NLB_Small` monthly** and **SSD block/PVC per-GB** — the only two estimated lines (console/quote); they cancel in the delta but set the absolute total.
2. **HCM03-1C** flavor availability + price parity (product-page prices are the national catalogue; the account's single enabled AZ is HCM03-1C).
3. **Saving Plan** vs PAYG for prod (committed discounts not modelled here).

---

## 7. Sources
- vServer flavor prices (published CMS table): `https://vngcloud.vn/vi/product/vserver`
- Managed PostgreSQL (RDS) + MemStore prices: `https://vngcloud.vn/vi/product/vdb`
- Object storage per-GB tiers: `https://vngcloud.vn/vi/product/vstorage`
- vDB storage 2,000 VND/GB (illustrative) + Kafka example: `https://docs.greennode.ai/vdb/kafka-cluster-kds/cach-tinh-phi`
- VKS control plane free: `https://docs.greennode.ai/vn/vks/cach-tinh-gia`
- Volume/IOPS tiers (unpriced): `https://docs.greennode.ai/vserver/compute-hcm03-1a/volume/volume-types`
- Current footprint: this repo's `deployments/*/overlays/{uat,prod}.tfvars` + module READMEs (technical doc §2).
- FX ≈ 26,000 VND/USD (external market, Aug 2026). Prices are VAT-inclusive PAYG list; a Saving Plan is cheaper.
