# LEO Customer 360

**AI-ready Customer 360 infrastructure for identity resolution, unified customer profiles, behavioral data, analytics, and activation.**

LEO Customer 360 is an open-source **Customer 360 and identity-resolution platform** designed as a foundation for a composable Customer Data Platform (CDP).

It brings fragmented customer data into a unified, tenant-aware customer model and provides the infrastructure required to build analytics, segmentation, personalization, scoring, AI, and marketing activation on top of that foundation.

> **Collect → Resolve → Unify → Understand → Activate**

- **Repository:** https://github.com/LEO-CDP/leo-customer360
- **Documentation:** https://leo-cdp.github.io/leo-customer360/

---

## Why LEO Customer 360?

Customer data is usually fragmented across websites, mobile applications, POS systems, CRM platforms, advertising platforms, and transactional systems.

The difficult problem is not simply storing this data.

The difficult problem is answering:

> **Who is this customer, what do we know about them, and what should the business do next?**

LEO Customer 360 addresses the foundation of that problem through:

- **Identity Resolution** — connect records belonging to the same real-world customer
- **Golden Customer Records** — maintain a unified customer profile
- **Identity Graph** — preserve relationships between identities, profiles, and business entities
- **Behavioral Data** — ingest and aggregate customer events
- **Segmentation** — create reusable customer audiences
- **Analytics** — expose customer and operational metrics
- **AI-ready Data** — provide structured customer context and vector-storage infrastructure for downstream ML/AI systems
- **Activation** — support approval-checked email and Zalo campaign dispatch, plus a separate promotions API

---

# Architecture

LEO Customer 360 follows a modular architecture built around PostgreSQL, object storage, and independently deployable application services.

```text
Sources ── behavioral events ──> customer360-event-api ──> Redis Streams
                                                              │
                                                              ▼
                                                      S3-compatible storage
                                                              │
                                                              ▼
                                                    Dagster analytics jobs

Sources ── profiles / CRM data ──> PostgreSQL <──> identity resolution
                                        │
                                        ├──> Customer 360 API and admin UI
                                        ├──> segmentation and analytics
                                        └──> campaign and promotions services
```

PostgreSQL 16 stores operational customer, identity, CRM, and application data. The tracking API validates event batches, durably enqueues them in Redis Streams, and writes immutable event objects to S3-compatible storage (MinIO in local development); it does not write tracking events directly to PostgreSQL. Dagster jobs process backend workflows, while FastAPI services expose APIs and the browser-based admin UI.

---

# Core Capabilities

| Capability | Description | Status |
|---|---|---|
| **Customer 360** | Unified master profiles and customer data | ✅ Available |
| **Identity Resolution** | Metadata-driven profile matching and consolidation | ✅ Available |
| **Identity Graph** | Relationships between identities and customer entities | ✅ Available |
| **Event Tracking** | Validated event ingestion, Redis queueing, and S3-compatible storage | ✅ Available |
| **Segmentation** | Segment membership computation and synchronization | ✅ Available |
| **Analytics** | Tracking-log aggregation and operational reporting | ✅ Available |
| **REST / MCP APIs** | Customer, identity, CRM, reporting, administration, and MCP interfaces | ✅ Available |
| **Multi-tenancy** | Tenant-aware API access and authorization | ✅ Available |
| **Authentication** | Local development bearer-token login and Keycloak SSO mode | ✅ Available |
| **Admin UI** | Browser-based Customer 360 administration | ✅ Available |
| **Promotions API** | Tenant-scoped placements, sponsored content, and recommendations | ✅ Available |
| **Campaign Activation** | Approval-checked email and Zalo campaign dispatch workflows | ✅ Available |
| **AI Campaign Planning** | Separate service that proposes email and Zalo campaign drafts | ✅ Initial service |
| **Vector Search** | pgvector schema and database support; customer search workflows are not included | 🧩 Infrastructure |
| **Customer Scoring** | Model registry and data structures; production scoring pipelines are not included | 🧩 Infrastructure |
| **Personalization** | Broader production personalization workflows | 🚧 Roadmap |

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

The complete documentation is available at:

**https://leo-cdp.github.io/leo-customer360/**

Recommended starting points:

- Architecture
- Customer 360 data model
- Identity Resolution
- API documentation
- Event Tracking API
- Dagster backend
- Docker Compose
- Kubernetes deployment
- Frontend administration
- Database schema
- Testing and troubleshooting

---

# Roadmap

LEO Customer 360 is evolving toward a broader AI-first Customer Data Platform.

## Foundation

- [x] Customer 360 golden records
- [x] Identity Resolution
- [x] Identity graph
- [x] Tenant-aware API
- [x] Event ingestion
- [x] Segmentation
- [x] Analytics infrastructure
- [x] Admin UI

## Intelligence

- [ ] Production scoring pipelines
- [ ] CLV modeling
- [ ] Churn prediction
- [ ] Lead scoring
- [ ] Propensity modeling
- [ ] Real-time customer state
- [ ] Advanced customer journey intelligence

## AI

- [x] AI-assisted email and Zalo campaign draft planning
- [ ] AI customer analyst
- [ ] Natural-language Customer 360 search
- [ ] Text-to-SQL analytics
- [ ] Semantic customer discovery
- [ ] Customer embeddings
- [ ] AI-powered segmentation
- [ ] AI recommendation engine
- [ ] End-to-end autonomous marketing workflows

## Activation

- [x] Approval-checked email and Zalo campaign dispatch
- [ ] Cross-channel campaign orchestration
- [ ] Marketing automation
- [ ] Personalization engine
- [ ] Next-best-action
- [ ] Multi-channel activation
- [ ] Experimentation and optimization

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
