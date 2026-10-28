# LEO Customer 360

**AI-ready Customer 360 infrastructure for identity resolution, unified customer profiles, behavioral data, analytics, and activation.**

LEO Customer 360 is an open-source **Customer 360 and identity-resolution platform** designed as a foundation for a composable Customer Data Platform (CDP).

It brings fragmented customer data into a unified, tenant-aware customer model and provides the infrastructure required to build analytics, segmentation, personalization, scoring, AI, and marketing activation on top of that foundation.

> **Collect → Resolve → Unify → Understand → Activate**

![Unified Journey Report](docs/marketing/images/unified-journey-report.png)


- **Repository:** https://github.com/LEO-CDP/leo-customer360
- **Documentation Site:** https://leo-cdp.github.io/leo-customer360/
- **Repository Docs Hub:** [`docs/README.md`](docs/README.md) · **Vietnamese README:** [`README-vn.md`](README-vn.md)

---

## Why LEO Customer 360?

Customer data is usually fragmented across websites, mobile applications, POS systems, CRM platforms, advertising platforms, and transactional systems.

The difficult problem is not simply storing this data.

The difficult problem is answering:

> **Who is this customer, what do we know about them, and what should the business do next?**

LEO Customer 360 addresses the foundation of that problem through:

- **Identity Resolution** — connect records belonging to the same real-world customer using metadata-driven matching and survivorship rules
- **Golden Customer Records** — maintain a unified, tenant-isolated customer profile (`cdp_master_profiles`) with domain extensions (`cdp_domain_profiles`)
- **Persona Resolution** — compute versioned customer personas (`cdp_customer_personas`) and shared archetypes (`cdp_persona_archetypes`) with explainable feature breakdowns
- **Identity Graph** — preserve relationships between raw identities, master profiles, and business entities
- **Behavioral Data Lake** — ingest high-throughput events via Redis Streams into immutable S3/MinIO `.jsonl.gz` partitions without touching PostgreSQL on the hot path
- **Segmentation & IAB Taxonomy** — build segments with visual QueryBuilder or natural-language AI drafts, and map standardized IAB Audience Taxonomy 1.1 tags
- **Governed Campaign Activation & Ad Sync** — execute human-approved multi-step campaign workflows across Email, Zalo ZNS, SMS, Web Push, and hashed custom-audience exports to Meta Ads, Google Ads Customer Match, and TikTok Ads

---

# Architecture

LEO Customer 360 follows a modular architecture built around PostgreSQL 16, Redis, S3-compatible object storage, and eight independently deployable `customer360-*` components.

```text
Web / Mobile SDK / Webhooks ──> customer360-event-api (:8010) ──> Redis Streams
                                                                       │
                                                                       ▼
                                                            S3 / MinIO (.jsonl.gz)
                                                                       │
                                                                       ▼
Sources / CRM / S3 Sync ──────> PostgreSQL 16 <──> customer360-backend (Dagster)
                                (customer360)      ├── analytics (S3 -> staging)
                                      │            ├── identity_resolution (CIR + personas)
                                      │            ├── segmentation (SQL rules + IAB tags)
                                      │            ├── data_synch (Meta/Google/TikTok Ads)
                                      │            ├── campaign_activation (FlowEngine)
                                      │            ├── email_engine & notification_engine (ZNS)
                                      │            └── ai_agents_runners
                                      ├──> customer360-api (:8008, REST + /mcp) <──> customer360-agent (:8009)
                                      ├──> customer360-frontend (:8890, Admin UI)
                                      └──> customer360-promotions (:9009, leo_ads schema)
```

PostgreSQL 16 stores operational customer, identity, persona, segmentation, CRM, workflow, and application data protected by Row-Level Security (RLS). [`customer360-event-api`](customer360-event-api/README.md) validates event batches, durably enqueues them in Redis Streams, and writes immutable `.jsonl.gz` objects to S3-compatible storage (MinIO in local development); it has zero database dependencies on the request path. Dagster orchestrates backend data pipelines in [`customer360-backend`](customer360-backend/README.md), while FastAPI services expose REST/MCP contracts and the browser-based Admin UI.

---

# Core Capabilities

| Capability | Description | Status |
|---|---|---|
| **Customer 360** | Unified master profiles (`cdp_master_profiles`), domain profiles, and 360° timelines | ✅ Available |
| **Identity Resolution (CIR)** | Metadata-driven exact/fuzzy matching, survivorship rules, merge history, and S3 event projections | ✅ Available |
| **Persona Resolution Engine** | Explainable persona scoring, versioned assignments, and lookalike centroid archetypes | ✅ Available |
| **Identity Graph** | Relationships between raw identities, master profiles, and customer entities | ✅ Available |
| **Event Tracking & Web SDK** | Cross-domain Web SDK (`leo.proxy.js` + `leo.observer.js`), Redis Streams handoff, and S3/MinIO storage | ✅ Available |
| **Segmentation & IAB Tags** | Visual QueryBuilder, AI natural-language rule drafting, scheduled recompute, and IAB Audience Taxonomy 1.1 mapping | ✅ Available |
| **Analytics** | S3 tracking-log aggregation, raw-profile staging extraction, and device/channel reporting | ✅ Available |
| **REST & MCP APIs** | Authenticated Customer 360 REST endpoints and tenant-scoped Model Context Protocol (`/mcp`) tools | ✅ Available |
| **Multi-tenancy & Security** | End-to-end `tenant_id` isolation enforced by API guards and PostgreSQL Row-Level Security (RLS) | ✅ Available |
| **Authentication & RBAC** | Local bearer-token login, Keycloak SSO integration, and role-based access control | ✅ Available |
| **Admin UI** | Browser-based Customer 360 administration, visual workflow editor, and built-in docs assistant | ✅ Available |
| **Campaign Workflow Activation** | Human-approved (`Draft → InReview → Approved`) multi-step workflow execution across Email, Zalo ZNS, SMS, and Push | ✅ Available |
| **Ad Audience Synchronization** | Privacy-safe (SHA-256 hashed) segment exports to Meta Custom Audiences, Google Customer Match, and TikTok Ads | ✅ Available |
| **AI Campaign Planning** | Dedicated `customer360-agent` service (LiteLLM + versioned prompt store) for Email & Zalo ZNS campaign drafts | ✅ Available |
| **Promotions API** | Tenant-scoped placements, creatives, sponsored content, and recommendations (`leo_ads` schema) | ✅ Available |
| **Vector Search & Embeddings** | `pgvector` schema for content and persona archetypes (`Vector(768)`) | ✅ Available |
| **Customer Scoring Catalog** | Feature/agent catalog (`cdp_ai_feature_catalog`, `cdp_ai_agents`) and score columns; automated ML training pipelines are roadmap items | 🧩 Foundation |

---

# Customer Identity Resolution

Identity resolution is the core of LEO Customer 360.

The platform can resolve multiple records into a unified customer identity using configurable metadata-driven matching rules.

For example:

```text
                    ┌───────────────┐
                    │ Anonymous Web │
                    │    Visitor    │
                    └───────┬───────┘
                            │
                         device_id
                            │
                            ▼
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│ Mobile App    │────▶│ Identity      │◀────│ POS           │
│ Profile       │     │ Resolution    │     │ Transaction   │
└───────────────┘     │ Engine (CIR)  │     └───────────────┘
                      └───────┬───────┘
                              │
                     email / phone /
                     customer_id /
                     device_id / ...
                              │
                              ▼
                    ┌─────────────────┐
                    │ Master Profile  │
                    │ Customer 360    │
                    └─────────────────┘
```

Matching rules are metadata-driven, allowing identity attributes and matching strategies to evolve without hard-coding every identifier into the resolver.

The platform also maintains lineage between master profiles and the raw records that contributed to them, including match method and match score.

---

# Customer 360 Data Model

The platform is built around a unified customer model containing:

- Master customer profiles
- Raw profiles
- Identity attributes
- Identity links
- Behavioral events
- CRM entities
- Personas
- Segments
- Customer relationships
- Customer scores
- Embeddings
- Operational metadata

The underlying PostgreSQL model supports conventional relational queries and graph-like customer relationships.

The schema includes `pgvector` support for embedding storage. Production customer-embedding generation and semantic customer search are not provided as end-to-end workflows.

---

# Event Tracking

LEO Customer 360 includes a dedicated tracking API for behavioral data.

The tracking layer is designed to separate:

```text
Event Collection
      ↓
Durable Queue
      ↓
Immutable Tracking Logs
      ↓
Analytics / Processing
      ↓
Customer Intelligence
```

The tracking API is an ingestion boundary, not a direct database writer. Its workers persist validated event batches as immutable objects; backend jobs and API read paths consume those objects separately from the PostgreSQL customer model.

Typical event sources include:

- Web
- Mobile
- POS
- E-commerce
- CRM
- Transactions
- Advertising platforms
- Social platforms
- External APIs

---

# Analytics & Segmentation

Customer 360 is not only an identity database.

The current platform includes:

- Tracking-log aggregation and device-type metrics
- Segment definitions and membership recomputation
- Customer profile timelines and CRM relationships
- Identity and operational reporting

Backend workflows include identity resolution, segmentation, tracking-log analytics, and bounded campaign activation, email, and notification processing. Trained scoring models and broad autonomous marketing workflows are not included.

---

# AI-Ready Customer Data

LEO Customer 360 is designed as an **AI-ready data foundation**, rather than attempting to make the Customer 360 database itself an AI product.

The platform provides structured customer context that can be consumed by downstream AI and machine-learning systems.

Potential downstream use cases include:

### Semantic Customer Search

```text
"Find customers similar to our highest-value
B2B customers with strong product engagement."
```

### Lookalike Discovery

```text
Customer Segment
       ↓
   Embedding
       ↓
Vector Similarity
       ↓
Similar Customers
```

### Customer Scoring

Potential scoring models include:

- Lead Conversion Probability
- Churn Probability
- Customer Lifetime Value
- Engagement Score
- Propensity Scores
- Next Best Action

The platform provides data structures and infrastructure that downstream systems can use. Model training, inference, and production customer-embedding pipelines remain separate work.

---

# Technology Stack

| Layer | Technology |
|---|---|
| Primary database | PostgreSQL 16 |
| Vector search | pgvector |
| Geospatial data | PostGIS |
| API | FastAPI |
| ORM / data access | SQLAlchemy |
| Workflow orchestration | Dagster |
| Cache / streaming queue | Redis |
| Authentication | Signed bearer tokens; Keycloak for SSO deployments |
| Object storage | S3 / MinIO |
| Frontend | FastAPI-served HTML/CSS/JavaScript admin UI |
| Containerization | Docker / Docker Compose |
| Deployment | Docker / Kubernetes |
| Backend language | Python |

The architecture deliberately uses open-source and composable infrastructure so individual components can be replaced or scaled independently.

---

# Repository Structure

```text
leo-customer360/
│
├── customer360-api/        # FastAPI REST and MCP service
├── customer360-agent/      # AI-assisted campaign planning service
├── customer360-backend/    # Dagster workspace with eight code locations
│   ├── identity_resolution/
│   ├── segmentation/
│   ├── analytics/
│   ├── ai_agents_runners/
│   ├── data_synch/
│   ├── campaign_activation/
│   ├── email_engine/
│   └── notification_engine/
├── customer360-dao/        # Shared persistence and data-access package
├── customer360-database/   # PostgreSQL schema, seeds, and migrations
├── customer360-event-api/  # Event ingestion to Redis Streams and S3
├── customer360-frontend/   # FastAPI-served admin UI
├── customer360-promotions/ # Promotions API and browser widget
├── customer360-seeding/    # Synthetic data and integration workflows
├── deployments/            # Deployment scripts and configuration
├── docs/                   # Technical and operational documentation
├── docs-site/              # Public documentation site source
├── k8s/                    # Kubernetes manifests and scripts
├── postgres/               # PostgreSQL image and configuration
├── redis/                  # Redis image and configuration
├── tools/                  # Supporting tools, including docs search
├── ui-wireframes/          # UI design references
├── docker-compose.yml
├── dev-docker-compose.yml
├── dev-no-sso-docker-compose.yml
├── dev-c360.sh
├── manage-c360.sh
└── run_all_tests.sh
```

---

# Quick Start

## 1. Clone

```bash
git clone https://github.com/LEO-CDP/leo-customer360.git
cd leo-customer360
```

## 2. Configure Environment

```bash
cp .env.example .env
```

Replace the `change_me_*` placeholder credentials in `.env` before starting services. The development script can create `.env` from `.env.example` if it is missing, but it cannot choose secure passwords for you.

## 3. Start the Local Development Environment

```bash
./dev-c360.sh
```

This script starts the local Docker dependencies and supporting services, runs the demo seed workflow when the database is empty, and restarts the host-run API, Dagster backend, and admin UI. The default API and admin UI ports are `8008` and `8890`; see `.env` for configuration.

For the Docker Compose-managed core stack instead, use:

```bash
./manage-c360.sh start
./manage-c360.sh status
```

The Compose-managed stack does not replace the host-run frontend started by `dev-c360.sh`.

Run the test suites registered in the repository's consolidated test runner:

```bash
./run_all_tests.sh
```

This script runs the DAO, API, event API, identity resolution, segmentation, campaign activation, email engine, and promotions suites; it does not run every test in every repository component.

For complete deployment instructions, see the official documentation.

---

# API & Authentication

The Customer 360 API is tenant-aware and uses bearer tokens. Local development defaults to `SSO_LOGIN=false`, which enables the configured development login; deployments can use Keycloak SSO with `SSO_LOGIN=true`.

Typical authentication flow:

```bash
curl -s -X POST \
  http://localhost:8008/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "admin",
    "password": "<password from .env>"
  }'
```

Use the returned `access_token` for subsequent API requests:

```http
Authorization: Bearer <access-token>
```

When `SSO_LOGIN=true`, use the Keycloak sign-in flow rather than the development `/auth/login` endpoint. Authentication and tenant context are enforced by the API.

---

# Documentation

The complete documentation is available online and inside this repository:

- **Online Documentation Site:** https://leo-cdp.github.io/leo-customer360/
- **Repository Documentation Index:** [`docs/README.md`](docs/README.md)
- **Vietnamese Overview:** [`README-vn.md`](README-vn.md)

### Component Guides

- [`customer360-database/README.md`](customer360-database/README.md) — PostgreSQL 16 schema, initialization order, migrations, and [Full Schema Diagram](customer360-database/full-schema-diagram.md)
- [`customer360-dao/README.md`](customer360-dao/README.md) — Shared models, schemas, tenant-aware repositories, RLS helpers, and `FlowEngine`
- [`customer360-event-api/README.md`](customer360-event-api/README.md) — Event envelope, Redis Streams, S3/MinIO storage, Web SDK, and [UAT Performance Report](customer360-event-api/tests/reports/PERF_REPORT.md)
- [`customer360-backend/README.md`](customer360-backend/README.md) — Dagster workspace, bounded batch controls, Redis leases, and 8 code locations
- [`customer360-api/README.md`](customer360-api/README.md) — REST & MCP surface, authentication, tenant isolation, and caching
- [`customer360-agent/README.md`](customer360-agent/README.md) — AI campaign planning service, LiteLLM provider config, and versioned prompts
- [`customer360-promotions/README.md`](customer360-promotions/README.md) — Ad placements, creatives, sponsored content, and recommendations
- [`customer360-frontend/README.md`](customer360-frontend/README.md) — Browser admin UI, workflow editor, and session handling
- [`deployments/README.md`](deployments/README.md) & [`k8s/README.md`](k8s/README.md) — VM, Terraform, reverse proxy, and Kubernetes deployment runbooks

### Topic Guides in `docs/`

- [System Architecture & Technical Documentation](docs/architecture/TECHNICAL-DOCUMENTATION.md)
- [Data Source Types (Web SDK, Connectors, Webhooks, S3 Batch, Mobile SDK)](docs/data-sources/data-source-types.md)
- [Customer Identity Resolution Readiness & Slides](docs/identity-resolution/CIR-improvement.md)
- [Marketing Workflow & Governance Specification](docs/marketing/MARKETING-WORKFLOW.md)
- [Docker Compose & Operations Guide](docs/operations/DOCKER-COMPOSE-GUIDE.md)

---

# Roadmap

LEO Customer 360 is evolving toward a broader AI-first Customer Data Platform.

## Foundation

- [x] Customer 360 golden records (`cdp_master_profiles` & `cdp_domain_profiles`)
- [x] Metadata-driven Identity Resolution & merge history
- [x] Explainable Persona Resolution Engine (`cdp_persona_archetypes` & `cdp_customer_personas`)
- [x] Identity graph & profile links
- [x] Tenant-aware REST & MCP APIs with PostgreSQL RLS
- [x] High-throughput Event Ingestion (Redis Streams → S3/MinIO `.jsonl.gz`)
- [x] Segmentation with AI natural-language drafting & IAB Audience Taxonomy 1.1
- [x] S3 Event Lake analytics & per-profile event projections
- [x] Admin UI & visual workflow builder

## Intelligence

- [ ] Automated ML model training & inference pipelines
- [ ] Production CLV, churn, and lead-scoring runners
- [ ] Real-time streaming customer state & feature store
- [ ] Advanced multi-touch attribution & journey analytics

## AI

- [x] AI-assisted Email and Zalo ZNS campaign draft planning (`customer360-agent`)
- [x] AI-assisted natural-language segment rule builder
- [x] Built-in documentation RAG assistant
- [ ] Conversational AI customer analyst & text-to-SQL analytics
- [ ] Automated customer embedding generation & lookalike discovery
- [ ] Autonomous closed-loop optimization agents

## Activation

- [x] Approval-checked campaign workflow execution (`campaign_activation`)
- [x] Email delivery with signed open/click tracking (`email_engine`)
- [x] Zalo OA / ZNS delivery & S3 opt-out projection (`notification_engine`)
- [x] SHA-256 hashed audience sync to Meta, Google Ads, and TikTok (`data_synch`)
- [x] Promotions & ad-serving API (`customer360-promotions`)
- [ ] Real-time event-triggered journey enrollment & durable wait timers
- [ ] Automated A/B/n experimentation and bandit optimization

---

# Design Principles

## 1. Identity Before Intelligence

AI and analytics are only as reliable as the customer identity underneath them.

> **Bad identity → bad customer intelligence → bad decisions.**

Identity resolution is therefore a first-class platform capability.

## 2. Data Before AI

The system focuses first on building reliable customer context.

```text
Events
  +
Profiles
  +
Transactions
  +
Relationships
  +
Identity
  +
Context
      ↓
Customer Intelligence
      ↓
AI
```

## 3. Metadata-Driven Architecture

Identity attributes, matching rules, segmentation metadata, and scoring metadata should be configurable rather than hard-coded wherever practical.

## 4. Composable Architecture

Services should be independently deployable and replaceable.

PostgreSQL, Redis, Dagster, object storage, APIs, and AI services can evolve independently.

## 5. Explainability and Lineage

Customer intelligence should be traceable.

A unified profile should answer:

- Where did this data come from?
- Which identities were merged?
- Which rule produced the match?
- What confidence did the resolver assign?
- Which system produced the score?
- When was the value updated?

## 6. AI-Ready, Not AI-Dependent

The platform should remain useful without an LLM.

AI should enhance customer intelligence rather than become a dependency for basic identity, data, and analytical operations.

---

# LEO CDP Ecosystem

LEO Customer 360 is a core component of the broader **LEO CDP** ecosystem.

The long-term architecture is:

```text
                         LEO CDP
                            │
             ┌──────────────┴──────────────┐
             │                             │
       DATA FOUNDATION             CUSTOMER INTELLIGENCE
             │                             │
             ▼                             ▼
      Data Collection              Identity Resolution
      Event Tracking               Customer 360
      Data Pipelines               Segmentation
      Data Quality                 Analytics
             │                             │
             └──────────────┬──────────────┘
                            │
                            ▼
                         AI LAYER
                            │
               ┌────────────┼────────────┐
               ▼            ▼            ▼
            Scoring     Prediction     Agents
               │            │            │
               └────────────┼────────────┘
                            ▼
                        ACTIVATION
                            │
               ┌────────────┼────────────┐
               ▼            ▼            ▼
           Marketing  Personalization  Advertising
```

The goal is to provide an open, modular foundation for organizations that want to build their own Customer Data Platform rather than depend entirely on a proprietary SaaS stack.

---

# Contributing

Contributions are welcome.

Areas where contributions are particularly valuable include:

- Identity resolution algorithms
- Data quality
- Customer scoring
- Analytics
- AI / embeddings
- Segmentation
- Personalization
- Campaign orchestration
- Data connectors
- API integrations
- Frontend components
- Documentation
- Testing

Before contributing, review the existing architecture and documentation to understand the service boundaries and data model.

---

# License

See [`LICENSE`](./LICENSE) for the license applicable to this repository.

---

## Links

- **Documentation:** https://leo-cdp.github.io/leo-customer360/
- **GitHub:** https://github.com/LEO-CDP/leo-customer360
- **LEO CDP:** https://leocdp.com/
