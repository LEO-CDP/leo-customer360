# Customer 360 — vServer → VKS Cost Analysis

> **Status:** research / decision-support · **Date:** 2026-08-26 · **Scope:** UAT + PROD
> **Companion:** [`vks-migration-technical-analysis.md`](./vks-migration-technical-analysis.md)
> **FX used:** ≈ **26,100 VND = 1 USD** (mid-market, week of 2026-08-19). All figures indicative.

## 0. Read this first — why there are few hard numbers

**VNG Cloud / GreenNode does not publish static price tables.** Every pricing page routes to
the interactive calculator (`calculator.console.greennode.ai`) or a sales quote. That means the
per-flavor monthly prices this analysis needs —
`s-general-1x2`, `s2-general-2x4`, `s2-general-4x8`, `s2-general-8x16`,
`db.s-general-2x4`, `db.s-general-8x16`, `NLB_Small`, SSD block-storage per-GB — are **not
publicly documented**. The **only** VNG-published compute/storage rate found anywhere is a
**Kafka** example (below), which is Kafka-specific and used here purely as an order-of-magnitude
anchor.

Therefore this document does two things:
1. **Quantifies what *can* be quantified without prices** — the resource footprint (vCPU, RAM,
   storage, load balancers) of the current setup vs the VKS target, per env. This is the real
   driver of cost and is exact.
2. Provides a **parametric cost model + a fill-in-the-blank rate table** so that, once you pull
   the six unit prices from the VNG calculator for **HCM03-1C**, the monthly TCO drops out
   immediately.

> ⚠️ **Do not treat any VND figure in §5 as VNG-confirmed.** Fill the rate table from the
> calculator/quote first. Only the Kafka anchor in §6 is a published VNG number.

---

## 1. Cost model

Monthly cost decomposes into the same buckets before and after — VKS only changes *how many* of
each you buy:

```
Total/mo =  Σ(worker-node VMs · flavor_price)          ← compute
          + Σ(PVC GB · block_storage_price)            ← in-cluster persistent volumes
          + Σ(load balancers · LB_package_price)       ← ingress / NLB
          + managed PostgreSQL (unchanged)
          + managed Redis/MemStore (unchanged)
          + object storage + egress (≈ unchanged)
          + VKS control plane .......................... = 0 (free)
```

Key structural facts that shape the model:
- **VKS control plane is free** — no per-cluster/hour charge (unlike GKE/EKS ≈ $73/cluster/mo).
- **Worker nodes are billed as ordinary vServer VMs** at the *same flavor prices you pay today*.
  So compute cost is driven purely by **how much vCPU/RAM you provision**, VM-or-K8s.
- **Managed PostgreSQL and MemStore are untouched** by the migration → their cost is a constant
  in both columns and can be ignored for the *delta*.
- **Each `Service type=LoadBalancer` provisions a billed NLB** → consolidate to **one** ingress LB.

**The migration's cost verdict is therefore decided by one question: does the VKS node pool
provision more or less vCPU/RAM than the VMs it replaces?**

---

## 2. Current footprint (exact)

### 2.1 UAT
| Item | Qty | Spec | vCPU | RAM | Disk |
|---|---|---|---|---|---|
| app VMs (`s-general-1x2`) | 3 | api + backend + tracking | **3** | **6 GB** | 3 × 20 GB |
| managed PostgreSQL | 1 | `db.s-general-2x4` | (2) | (4) | 20 GB |
| Redis | 0 | container on api box | — | — | — |
| L4 NLB | 1 | `NLB_Small` | — | — | — |
| **App-compute subtotal** | | | **3 vCPU** | **6 GB** | |

### 2.2 PROD (as provisioned today — 4 boxes)
| Item | Qty | Spec | vCPU | RAM | Disk |
|---|---|---|---|---|---|
| api (`s2-general-4x8`) | 1 | 4/8 | 4 | 8 | 50 GB |
| sso (`s2-general-2x4`) | 1 | 2/4 | 2 | 4 | 50 GB |
| frontend (`s2-general-2x4`) | 1 | 2/4 | 2 | 4 | 50 GB |
| ads (`s2-general-4x8`) | 1 | 4/8 | 4 | 8 | 50 GB |
| managed PostgreSQL | 1 | `db.s-general-8x16` | (8) | (16) | 250 GB |
| managed MemStore | 1 | `db.s-general-2x4` | (2) | (4) | — |
| L4 NLB | 1 | `NLB_Small` | — | — | — |
| **App-compute subtotal (provisioned)** | | | **12 vCPU** | **24 GB** | |
| **App-compute subtotal (design-complete: + tracking 2/4 + backend ~2/4)** | | | **~16 vCPU** | **~32 GB** | |

---

## 3. VKS target footprint (proposed)

Sizing rule of thumb: sum the workloads' realistic requests, add ~15–20% for the node OS +
kubelet + DaemonSets (CNI, kube-proxy, CSI node, ingress, Netdata) + scheduling headroom.

### 3.1 UAT — node pool
Today's 3 vCPU/6 GB is **oversubscribed** (the api box alone runs ~11 containers on 1 vCPU/2 GB).
Right-sized in K8s:
| Option | Node pool | vCPU | RAM | HA | Note |
|---|---|---|---|---|---|
| **A (recommended)** | 1 × `s2-general-4x8` (autoscale →2) | 4 | 8 | none (uat ok) | more real capacity than today, still tiny |
| B | 2 × `s2-general-2x4` | 4 | 8 | node-level | survives one node drain |

- PVCs: Jaeger badger (~5–10 GB) + pgAdmin data (~1–2 GB) ≈ **~10–15 GB SSD** (only if not kept ephemeral).
- Load balancers: **1** (ingress). Managed PostgreSQL unchanged; Redis → keep in-cluster (tiny) or adopt managed.

### 3.2 PROD — node pool
Bin-packing the 4–6 dedicated boxes into a shared pool (isolation via requests/limits, not per-VM):
| Option | Node pool | vCPU | RAM | HA | Note |
|---|---|---|---|---|---|
| **A (recommended)** | 2 × `s2-general-4x8` (autoscale 2→4) | 8–16 | 16–32 | node-level | headroom for ads bursts via autoscale |
| B | 2 × `s2-general-8x16` | 16 | 32 | node-level | matches design-complete footprint 1:1 |

- PVCs: Jaeger badger (~20 GB) + pgAdmin (~2 GB) ≈ **~20–25 GB SSD**.
- Load balancers: **1** (ingress). Managed PostgreSQL (`8x16`) + MemStore (`2x4`) unchanged.

---

## 4. Footprint delta (the part that *is* certain)

| | UAT now | UAT VKS (A) | PROD now (prov.) | PROD now (design) | PROD VKS (A) |
|---|---|---|---|---|---|
| Worker vCPU | 3 (oversub.) | **4** | 12 | ~16 | **8–16** (autoscale) |
| Worker RAM | 6 GB (oversub.) | **8 GB** | 24 GB | ~32 GB | **16–32 GB** |
| Load balancers | 1 | 1 | 1 | 1 | **1** |
| Block-storage PVCs | 0 | ~10–15 GB | 0 | 0 | ~20–25 GB |
| Control-plane fee | n/a | **0** | n/a | n/a | **0** |
| Managed PG / Redis | same | same | same | same | same |

**Interpretation**
- **UAT**: VKS provisions *slightly more* vCPU/RAM than today (4/8 vs an oversubscribed 3/6) plus
  a little PVC — a **small cost increase**, buying real capacity, HPA, and reliability the current
  1 vCPU box can't give. UAT is small either way.
- **PROD**: VKS provisions **the same or less** worker vCPU/RAM than the dedicated boxes, because
  bin-packing removes the per-service isolation waste (4–6 boxes sized for peak → a shared pool
  sized for aggregate). **Compute is roughly flat-to-cheaper**; you add only a modest PVC line and
  keep one LB. **Control-plane is free**, so there is no Kubernetes "tax" on top.
- **Net:** infra cost is expected **≈ flat overall** (UAT slightly up, PROD flat-to-down), with the
  real savings landing in **operations** (§7).

---

## 5. Fill-in rate table (pull from the VNG calculator for HCM03-1C)

Get these six numbers from `calculator.console.greennode.ai` (or a VNG quote), then compute totals.
Unit prices are **identical** for a VM and a VKS worker node of the same flavor — so this table
also re-prices the *current* setup.

| Line item | Unit | Unit price (VND/mo) | UAT qty | PROD qty |
|---|---|---|---|---|
| `s-general-1x2` (1/2) | node | `____` | current: 3 | — |
| `s2-general-2x4` (2/4) | node | `____` | VKS-B: 2 | current: 2 |
| `s2-general-4x8` (4/8) | node | `____` | VKS-A: 1–2 | current: 2 · VKS-A: 2–4 |
| `s2-general-8x16` (8/16) | node | `____` | — | VKS-B: 2 |
| Block storage SSD | GB/mo | `____` | ~10–15 GB | ~20–25 GB |
| Load balancer (`NLB_Small`) | LB/mo | `____` | 1 | 1 |
| Managed PostgreSQL `db.s-general-2x4` | inst/mo | `____` | 1 | — |
| Managed PostgreSQL `db.s-general-8x16` | inst/mo | `____` | — | 1 |
| Managed MemStore `db.s-general-2x4` | inst/mo | `____` | — (uat: in-cluster) | 1 |
| Object storage + egress | GB/mo | `____` | usage | usage |
| **VKS control plane** | cluster | **0 (free)** | 1 | 1 |

**Then:** `Total = Σ(qty × unit price)` per column. Compare the *current* column (VMs) to the *VKS*
column (worker nodes + PVC) — the managed-DB/Redis/object-storage rows cancel out in the delta.

---

## 6. The one published anchor (order-of-magnitude only)

The **only** VNG-published compute+storage rate located:
- **vDB Kafka (KDS): 1,500,000 VND/mo for 4 vCPU / 16 GB**, storage **2,000 VND/GB/mo**
  (source: docs.greennode.ai/vdb/kafka-cluster-kds/cach-tinh-phi).

⚠️ This is **Kafka-specific** and must **not** be assumed equal to vServer worker-node flavors or
to RDS/MemStore. Used *only* to sanity-check magnitude: if a 4/16 managed Kafka node is ~1.5M
VND/mo (~$57), general-purpose worker VMs are plausibly in the low-hundreds-of-thousands to
~1M VND/mo range per node — but **confirm with the calculator**. Block storage at ~2,000 VND/GB/mo
(~$0.077) implies the PROD PVC line (~25 GB) is ~**50,000 VND/mo (~$2)** — negligible either way.

---

## 7. Total cost of ownership (the decisive, non-price factor)

Infra cost is ≈ flat; the migration pays off in **operations**. These are qualitative but real:

| Dimension | vServer today | VKS | Effect |
|---|---|---|---|
| Deploys | SSH bash pipeline (`deploy-all.sh`), manual ordering, `docker run` per box | declarative manifests / Helm / GitOps, controller reconciliation | fewer failed/partial deploys; faster rollback |
| Scaling | resize a whole VM (downtime) | HPA + cluster autoscaler (ads bursts) | pay for peak only when needed |
| Reliability | container dies → `--restart`; box dies → manual | pod reschedule + node auto-repair | less manual firefighting |
| Networking | hand-maintained cross-box security groups, guessed IPs | Service DNS + NetworkPolicy | fewer misconfig outages |
| TLS / routing | Caddy + cutover runbook + `set-domain.sh` | Ingress + cert-manager | one mechanism, auto-renew |
| Ops access | Portainer + agents per box | `kubectl` | no extra tool to run/secure |
| Env parity | separate `uat`/`prod` tfvars sprawl + placeholders | one manifest set, values per env | drift/placeholder bugs (see tech doc §7) removed |
| Utilisation | dedicated boxes sized for peak isolation (idle waste) | bin-packing | better $/workload, esp. PROD |

**Cost of the migration itself** (one-time): engineering to write manifests/Helm + platform
add-ons + cutover + CI change for `data-tracking-api`; run UAT and PROD in parallel briefly during
cutover (temporary double-run). No new licensing (VKS control plane free; NGINX/cert-manager/
oauth2-proxy are OSS).

---

## 8. Verdict & recommendation

- **Infra spend:** expected **≈ neutral overall** — UAT ticks up slightly (buys real capacity),
  PROD is **flat-to-cheaper** via bin-packing; VKS's free control plane means no Kubernetes premium.
- **Operational spend:** **materially lower** — the migration deletes an entire class of VM-era
  toil (SSH deploys, cross-box firewalls, Caddy cutovers, Portainer, IP juggling).
- **Recommendation:** **Proceed**, UAT first (low risk, exercises the full pattern), then PROD with
  the same manifests. **Before committing budget, pull the six unit prices in §5 from the VNG
  calculator for HCM03-1C and confirm flavor availability + AZ (HCM03-1C only ⇒ no cross-AZ HA)
  with VNG.**

### Open pricing items to get from VNG (calculator or sales)
1. vServer flavor monthly prices: `s-general-1x2`, `s2-general-2x4`, `s2-general-4x8`, `s2-general-8x16`.
2. Block-storage SSD / SSD-IOPS per-GB-month.
3. Load-balancer package price (`NLB_Small`) + egress VND/GB.
4. Managed PostgreSQL (`db.s-general-2x4`, `db.s-general-8x16`) and MemStore (`db.s-general-2x4`).
5. vCR (registry) storage/pricing, if moving off GHCR.
6. **HCM03-1C** parity — public docs price/flavor pages cover HCM03-1A only.

---

## 9. Sources
- VKS control-plane-free, worker-nodes-as-VMs, LB-per-service billing, CSI, release schedule: **docs.greennode.ai/vks/**.
- Kafka published rate: **docs.greennode.ai/vdb/kafka-cluster-kds/cach-tinh-phi**.
- Pricing calculator (no static tables): **calculator.console.greennode.ai**.
- Current footprint: this repo's `deployments/*/overlays/{uat,prod}.tfvars` + module READMEs (see the technical doc §2).
- FX ≈ 26,100 VND/USD (mid-market, 2026-08-19 week).
