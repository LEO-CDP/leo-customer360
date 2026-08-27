
# Customer 360

Customer 360 là lớp identity resolution và golden record cho nền tảng LEO CDP. Repository này kết hợp customer graph sử dụng PostgreSQL, API FastAPI hỗ trợ đọc/ghi, workspace orchestration của Dagster và admin frontend dạng gọn sử dụng API.

Code trong repository này không phải là một abstract demo. Đây là layout thực tế của platform, bao gồm:

- schema PostgreSQL 16 cho master profiles, raw profiles, links, CRM entities, personas và segmentation metadata
- FastAPI API trong `customer360-api/` cho CRUD, reporting, auth và tenant-scoped access
- Dagster workspace trong `backend-system/` chạy các job identity resolution, segmentation và analytics
- FastAPI ad-serving service trong `ads-server/`
- browser-based admin UI trong `frontend-admin/` gọi API qua HTTP
- các script khởi động bằng Docker và seed demo ở thư mục gốc của repository

![](./docs/images/composable-cdp-architecture.png)

## Những gì đã được triển khai

Repository hiện có bốn application service, ba Dagster job đang hoạt động và sáu Dagster job dạng placeholder.

| Khu vực | Trạng thái | Ghi chú |
|---|---|---|
| `backend-system/identity_resolution/` | Đã triển khai | Dagster identity-resolution job; chuyển các raw profile match thành master profile |
| `backend-system/segmentation/` | Đã triển khai | Tính toán lại các segment đang hoạt động và đồng bộ dữ liệu member/tag về master profile |
| `backend-system/analytics/` | Đã triển khai | Dagster job chạy mỗi giờ để aggregate tracking logs và cập nhật source totals |
| `customer360-api/` | Đã triển khai | REST API chính cho identity, CRM, persona, reporting và metadata |
| `data-tracking-api/` | Đã triển khai | Lưu tracking event bất biến vào các object S3/MinIO theo từng source và từng giờ |
| `ads-server/` | Đã triển khai | FastAPI ad-serving API đa tenant với placement, campaign, creative và browser loader |
| `frontend-admin/` | Đã triển khai | FastAPI shell cho UI, sử dụng client-side JS và API request |
| `backend-system/scoring/`, `data_synch/`, `email_engine/`, `notification_engine/`, `campaign_activation/`, `personalization/` | Placeholder | Các Dagster scaffold có thể chạy, sẵn sàng bổ sung service logic |

## Cấu trúc repository

| Đường dẫn | Mục đích |
|---|---|
| [`database-init/`](database-init) | Nguồn schema: `database-schema.sql`, seed/init scripts và SQL views |
| [`backend-system/`](backend-system) | Dagster workspace với chín code location: identity resolution, segmentation, analytics và sáu placeholder service |
| [`customer360-api/`](customer360-api) | FastAPI service với router, auth, SQLAlchemy model và business logic |
| [`data-tracking-api/`](data-tracking-api) | FastAPI ingestion service ghi tracking-log object theo giờ vào S3/MinIO |
| [`ads-server/`](ads-server) | FastAPI ad-serving service độc lập với database, cache và widget code riêng |
| [`frontend-admin/`](frontend-admin) | Admin UI gọn, được FastAPI phục vụ và load từ static template |
| [`all-data-simulator/`](all-data-simulator) | Công cụ tạo raw data tổng hợp và hỗ trợ upload lên S3/MinIO |
| [`deployments/`](deployments) | Deployment script, cấu hình infrastructure component và deployment diagram |
| [`k8s/`](k8s) | Tài liệu và manifest triển khai Kubernetes |
| [`postgres/`](postgres), [`redis/`](redis) | Cấu hình Docker image tùy chỉnh cho PostgreSQL/PostGIS/pgvector và Redis |
| [`docs/`](docs) | Tài liệu về architecture, operations và planning |
| [`ui-wireframes/`](ui-wireframes) | Tài liệu tham khảo về UI design |
| `docker-compose.yml`, `dev-docker-compose.yml`, `dev-no-sso-docker-compose.yml` | Các Compose stack cho production-style, local-development và no-SSO |
| `dev-c360.sh`, `dev-stop-and-delete-all.sh`, `manage-c360.sh`, `run_all_tests.sh` | Script khởi động local, cleanup, quản lý stack và chạy test tổng hợp |

## Runtime architecture

Repository vận hành theo flow tổng quát sau:

1. Postgres lưu schema `customer360` canonical và toàn bộ operational metadata.
2. `customer360-api` cung cấp public API và thực thi tenant-aware auth qua middleware.
3. Dagster backend load chín code location; identity resolution, segmentation và analytics đang hoạt động, trong khi sáu service location khác là placeholder có thể chạy được.
4. Data-tracking API lưu các batch NDJSON bất biến theo giờ vào S3 bucket theo từng source; môi trường dev sử dụng MinIO trong cùng network.
5. Admin frontend gọi API trực tiếp và không truy cập database.
6. Ads server độc lập cung cấp tenant-scoped ad placement và creative thông qua API và browser loader.
7. Các script local dev và production bootstrap infrastructure chạy bằng Docker và vận hành platform như một stack thống nhất.

## Bắt đầu phát triển local

### 1) Chuẩn bị environment

```bash
cp .env.example .env
```

Sau đó chỉnh các giá trị trong `.env` cho Postgres, Redis, Keycloak và host port trên máy local. Repository có env template đầy đủ và các ghi chú vận hành trong thư mục docs.

### 2) Khởi động dev stack

```bash
./dev-c360.sh
```

Script này khởi động infra stack và data-tracking API sử dụng MinIO trong Docker, sau đó tự động seed demo data nếu database đang trống. Workflow này dành cho trường hợp Postgres và Redis chạy trong Docker, còn API chính và backend worker chạy trực tiếp trên host.

### 3) Chạy host service

Trong một terminal riêng, khởi động API và backend worker:

```bash
cd customer360-api
./start.sh

cd ../backend-system/identity_resolution
./run-demo.sh
```

Admin frontend cũng có thể được khởi động riêng:

```bash
cd ../frontend-admin
./start.sh
```

## Production-style stack

For the packaged stack using Docker Compose, run:

```bash
./manage-c360.sh start
./manage-c360.sh status
```

Stack này bao gồm production service stack chính trong `docker-compose.yml`, gồm Postgres, Redis, Keycloak, Dagster, Customer 360 API và data-tracking API. Dev stack trong `dev-docker-compose.yml` chạy thêm MinIO và initializer của MinIO. Service độc lập `ads-server/` có các startup script riêng.

## Service entrypoints

Repository sử dụng các entrypoint chính sau:

- `customer360-api/app.py` — FastAPI API entrypoint
- `data-tracking-api/app.py` — CDP tracking-log FastAPI entrypoint (port 8010)
- `ads-server/app.py` — ad-serving FastAPI entrypoint (port 9009 theo mặc định)
- `backend-system/workspace.yaml` — Dagster workspace chứa toàn bộ chín backend code location
- `backend-system/identity_resolution/worker.py` — local polling helper legacy; production chạy Dagster job
- `frontend-admin/app.py` — admin frontend shell
- `manage-c360.sh` — production-style Docker stack manager
- `dev-c360.sh` — local dev infrastructure bootstrap

## Authentication và tenant context

API được bảo vệ bằng auth trên gần như mọi route. Implementation hiện tại yêu cầu bearer token hợp lệ trên tất cả endpoint không được exempt, đồng thời xác định tenant/user context từ token hoặc Keycloak-backed auth flow. Code hiện chủ động từ chối các cách login chỉ dựa trên header, để runtime behavior khớp với API docs hiện tại trong `customer360-api/customer360-api.md`.

Flow local thông thường:

```bash
curl -s -X POST http://localhost:8008/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<password from .env>"}'
```

Sau đó truyền `access_token` nhận được dưới dạng bearer token trong các request tiếp theo.

## Testing

Repository có consolidated test runner:

```bash
./run_all_tests.sh
```

Đây là test entrypoint cấp project hiện tại cho các test suite của Customer 360 API, identity resolution, segmentation và LEO ad server. Data-tracking API cũng có runner riêng tại `data-tracking-api/run_unit_tests.sh`.

## Tài liệu chính

Đọc các tài liệu sau để có thêm context:

- [`docs/TECHNICAL-DOCUMENTATION.md`](docs/TECHNICAL-DOCUMENTATION.md)
- [`docs/DOCKER-COMPOSE-GUIDE.md`](docs/DOCKER-COMPOSE-GUIDE.md)
- [`customer360-api/customer360-api.md`](customer360-api/customer360-api.md)
- [`data-tracking-api/README.md`](data-tracking-api/README.md)
- [`ads-server/README.md`](ads-server/README.md)
- [`backend-system/README.md`](backend-system/README.md)
- [`frontend-admin/README.md`](frontend-admin/README.md)
- [`deployments/README.md`](deployments/README.md)
- [`k8s/README.md`](k8s/README.md)

## Tham khảo

- [LEOCDP.com](https://leocdp.com)
- [Dagster](https://dagster.io)
- [FastAPI](https://fastapi.tiangolo.com/)
- [PostgreSQL](https://www.postgresql.org/)
- [pgvector](https://github.com/pgvector/pgvector)
- [PostGIS](https://postgis.net/)
