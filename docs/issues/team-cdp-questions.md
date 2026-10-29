# Team CDP — Câu hỏi & câu trả lời

Trích từ **Sổ câu hỏi mở SCOPE** (NaviMS · SCOPE), ngày 21/10/2026. Tổng **21** câu hỏi: **7** đã trả lời, **14** còn mở.

| Mã | Trạng thái | Hạn | Đặc tả | Chủ đề |
|---|---|---|---|---|
| [Q01](#q01) | Đã trả lời | 16/10/2026 | S0 §1.2 | Định dạng thực tế của visitor_id, profile_id, session_id trong LeoCDP; cách đọc chúng từ J… |
| [Q02](#q02) | Đã trả lời | 16/10/2026 | S0 §2.3 | LeoCDP có giữ nguyên event_id, consent và is_test khi chuyển event vào hàng đợi và ClickHo… |
| [Q03](#q03) | Đã trả lời | 16/10/2026 | S0 I8, I10, §9.7 | LeoCDP xuất và nhận snapshot profile qua API hay file? Có phát event khi segment của một p… |
| [Q04](#q04) | Đã trả lời | 16/10/2026 | S0 §5 | Hàng đợi dùng Redis có sẵn của LeoCDP hay cần Kafka riêng? Mức tải LeoCDP collector chịu đ… |
| [Q05](#q05) | Đã trả lời | 16/10/2026 | S1 FR-01, FR-02 | LeoCDP tag có đọc được window.scopeDataLayer và hỗ trợ event tuỳ biến có thuộc tính lồng n… |
| [Q06](#q06) | Đã trả lời | 16/10/2026 | S1 FR-03 | Khi người dùng từ chối, tag có còn gửi request nào (ví dụ lấy cấu hình) không? Request đó … |
| [Q40](#q40) | Đã trả lời | 23/10/2026 | S0 §12.1 | LeoCDP có tự xoá profile không hoạt động theo một thời hạn cấu hình được (đề xuất mặc định… |
| [Q57](#q57) | Còn mở | 30/10/2026 | S0 §1.2, I8, S5 FR-12 | Mục tiêu: SCOPE và LeoCDP cùng nhận ra một người là một người. Nếu không, mỗi bên giữ một … |
| [Q58](#q58) | Còn mở | 06/11/2026 | S0 §5, I3 | (1) Đo tải bền (không phải tăng dần) với event thật ở mức 3 lần đỉnh giờ quay của xosodaip… |
| [Q59](#q59) | Còn mở | 30/10/2026 | S5 FR-12, I29 | Hợp nhất profile: LeoCDP gộp profile theo những khoá nào (email, SĐT, user_id, Zalo ID, mã… |
| [Q60](#q60) | Còn mở | 30/10/2026 | S0 §2.3 | Nạp nguồn ngoài website: LeoCDP có sẵn kết nối Fanpage Facebook, Zalo OA, YouTube, TikTok,… |
| [Q61](#q61) | Còn mở | 30/10/2026 | S5 FR-17, I29 | Tạo segment: marketing tự tạo segment trên giao diện LeoCDP được không (điều kiện theo hàn… |
| [Q62](#q62) | Còn mở | 30/10/2026 | S0 I13, I29 | Kích hoạt ra ngoài: LeoCDP có đẩy tệp khách hàng lên Meta Custom Audience, Google Customer… |
| [Q63](#q63) | Còn mở | 30/10/2026 | S5 FR-12, FR-17, I29 | Lấy dữ liệu ra cho SCOPE: SCOPE đọc profile, bảng nối định danh và danh sách thành viên se… |
| [Q64](#q64) | Còn mở | 30/10/2026 | S0 §9.7, I29 | Quyền dữ liệu và lối ra: Nam Việt xuất được toàn bộ dữ liệu (profile, định danh, segment, … |
| [Q65](#q65) | Còn mở | 30/10/2026 | S0 §2.5, §7.4, §12, I29 | SCOPE sẽ gửi sang LeoCDP 3 loại dữ liệu. LeoCDP nhận bằng cách nào (API hay file trên S3/M… |
| [Q66](#q66) | Còn mở | 30/10/2026 | S5 FR-05, S0 §11.2 | Event website chỉ vào kho dữ liệu của SCOPE qua file JSONL của LeoCDP trên S3/MinIO, nên k… |
| [Q71](#q71) | Còn mở | 13/11/2026 | S5 FR-18, I29 | Khi bật nối chéo site (Q68), SCOPE sẽ biết hai mã khách là một người, ví dụ: visitor v_3kQ… |
| [Q72](#q72) | Còn mở | 30/10/2026 | S1 NFR-01, S10 FR-14 | SCOPE đặt ngân sách cho phần LeoCDP trên trang: ≤ 30 ms luồng chính, ≤ 40 KB (gzip), ≤ 3 r… |
| [Q78](#q78) | Còn mở | 30/10/2026 | S0 §1.1c; S1 NFR-01; S10 FR-14 | LeoCDP dùng những tên miền nào trên trang và máy chủ? (1) Nơi phục vụ leo.proxy.js và các … |
| [Q85](#q85) | Còn mở | 30/10/2026 | S0 §7.4; I29; Q62, Q67 | LeoCDP có lọc được theo trạng thái đồng ý ads_offsite (tách khỏi ads) trước khi đẩy tệp lê… |

---

## Q01

**Trạng thái:** Đã trả lời · **Hạn:** 16/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §1.2  
**Ảnh hưởng tới:** S1, S3, Decision API  

### Câu hỏi

Định dạng thực tế của visitor_id, profile_id, session_id trong LeoCDP; cách đọc chúng từ JavaScript trên trang; tên cookie và domain của visitor_id (subdomain go. có đọc được không).

### Câu trả lời

- **Phản hồi của Team CDP (07/10):**
  - `visitor` (`anonymous_id`): 32 ký tự hex do SDK sinh, hoặc lấy nguyên `window.injectedVisitorId` nếu trang inject; lưu trong `localStorage` của iframe LeoCDP (không phải cookie), trang chính và subdomain `go.` không đọc trực tiếp được, chỉ đọc bất đồng bộ qua `LeoObserverProxy.getAnonymousId()`.
  - `session_id`: UUID v5 (kết hợp từ `time bucket` + `device_fingerprint` + `anonymous_id`).
  - `profile_id`: Web Tag không có `profile_id`; backend dùng `master_profile_id` (UUID); cầu nối `event → profile staging` tại thời điểm 07/10 đang hoàn thiện.
- **Chốt phương án với SCOPE (07/10):**
  - SCOPE tự sinh `visitor_id` (`v_` + 22 ký tự base62, lưu cookie `scope_vid` trên domain gốc của site để `go.` đọc được) và `session_id`, sau đó inject vào LeoCDP (S0 v1.20 §1.2, S10 FR-14). Việc tiếp theo: xem [Q57](#q57).
- **Mã nguồn & Tài liệu liên quan:**
  - Code: [`leo.proxy.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.proxy.js), [`leo.observer.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.observer.js), [`cdp-event-proxy.html`](../../customer360-event-api/static/c360-web-sdk/html/cdp-event-proxy.html).
  - Tài liệu: [`docs/data-sources/1-web-sdk-tracking.md`](../data-sources/1-web-sdk-tracking.md).

---

## Q02

**Trạng thái:** Đã trả lời · **Hạn:** 16/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §2.3  
**Ảnh hưởng tới:** S1, S5 (lọc trùng, đồng ý, dữ liệu thử)  

### Câu hỏi

LeoCDP có giữ nguyên event_id, consent và is_test khi chuyển event vào hàng đợi và ClickHouse không? Trường nào không giữ được?

### Câu trả lời

- **Phản hồi của Team CDP (07/10):**
  - **Luồng lưu trữ:** Tracking API → Redis Streams → file `.jsonl.gz` trên S3/MinIO (lưu đầy đủ mọi event).
  - **Các trường dữ liệu:**
    - `event_id`: Giữ nguyên ở lớp vỏ envelope (lấy giá trị client gửi lên).
    - `consent`, `is_test`: Giữ nguyên trong `payload` gốc nếu nằm trong event data (không tách thành cột riêng).
    - Bảng staging PostgreSQL (`cdp_raw_profiles_stage`) chỉ giữ bản mới nhất theo định danh. Repo không dùng bộ ghi ClickHouse.
- **Chốt phương án với SCOPE (07/10):**
  - Envelope của SCOPE đặt trong đối tượng `scope` của event; S5 tự viết loader đọc file JSONL trên S3/MinIO mỗi 5 phút, lọc trùng theo `event_id` và lọc theo `consent` (S0 §2.3, S5 FR-05).
- **Mã nguồn & Tài liệu liên quan:**
  - Code: [`storage.py`](../../customer360-event-api/core/storage.py) (`build_tracking_object`), [`service.py`](../../customer360-event-api/core/service.py) (`_enrich_events`).
  - Tài liệu: [`customer360-event-api/README.md`](../../customer360-event-api/README.md).

---

## Q03

**Trạng thái:** Đã trả lời · **Hạn:** 16/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) I8, I10, §9.7  
**Ảnh hưởng tới:** S5, Decision API  

### Câu hỏi

LeoCDP xuất và nhận snapshot profile qua API hay file? Có phát event khi segment của một profile thay đổi và khi gộp hai profile không?

### Câu trả lời

- **Phản hồi của Team CDP (07/10):**
  - Đã có REST API đọc/sửa từng profile đơn lẻ; chưa có cơ chế xuất/nhập snapshot hàng loạt.
  - Không phát sự kiện (webhook/event) khi profile, segment hoặc trạng thái gộp thay đổi; chưa có quy trình gộp hai master profile trực tiếp.
- **Chốt phương án với SCOPE (07/10):**
  - Segment tính ở ClickHouse (S5 FR-17), S5 ghi cache Decision API và phát `profile_updated` / `profile_merged`; LeoCDP cung cấp định danh qua bảng nối `visitor → profile` mỗi giờ (xem [Q57](#q57), [Q63](#q63)).
- **Mã nguồn & Tài liệu liên quan:**
  - Code: [`identity_api.py`](../../customer360-api/core/routers/identity_api.py), [`segment_api.py`](../../customer360-api/core/routers/segment_api.py).

---

## Q04

**Trạng thái:** Đã trả lời · **Hạn:** 16/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §5  
**Ảnh hưởng tới:** B1 hạ tầng, S3  

### Câu hỏi

Hàng đợi dùng Redis có sẵn của LeoCDP hay cần Kafka riêng? Mức tải LeoCDP collector chịu được ở đỉnh giờ quay số xosodaiphat.

### Câu trả lời

- **Phản hồi của Team CDP (07/10):**
  - Hàng đợi dùng **Redis Streams** (không dùng và không cần thêm Kafka).
  - Sức chứa tối đa `100.000` lô trong stream (`TRACKING_STREAM_MAX_LENGTH=100000`), tối đa `1.000` event/request (`TRACKING_MAX_EVENTS_PER_REQUEST=1000`); khi đầy sẽ trả lỗi từ chối để client gửi lại.
  - Số đo UAT theo kiểu tăng dần đạt ~105 RPS; giới hạn mặc định là `120 request / IP / 60 giây`.
- **Chốt phương án với SCOPE (07/10):**
  - SCOPE không đọc trực tiếp Redis của LeoCDP; topic của SCOPE dùng Redis Streams riêng. Phần đo tải đỉnh `xosodaiphat` và giới hạn theo IP xem tiếp tại [Q58](#q58).
- **Mã nguồn & Tài liệu liên quan:**
  - Code: [`redis_queue.py`](../../customer360-event-api/core/redis_queue.py), [`config.py`](../../customer360-event-api/core/config.py).
  - Tài liệu: [`PERF_REPORT.md`](../../customer360-event-api/tests/reports/PERF_REPORT.md).

---

## Q05

**Trạng thái:** Đã trả lời · **Hạn:** 16/10/2026 · **Đặc tả:** [S1](https://claude.ai/artifact/NuEsMpQVLm9JqCiGRmWBfj) FR-01, FR-02  
**Ảnh hưởng tới:** S1  

### Câu hỏi

LeoCDP tag có đọc được window.scopeDataLayer và hỗ trợ event tuỳ biến có thuộc tính lồng nhau (items[]) không?

### Câu trả lời

- **Phản hồi của Team CDP (07/10):**
  - SDK không tự động quét data layer ngầm; cần gọi `LeoObserverProxy.watchDataLayer({ dataLayerName, eventMap })` để theo dõi hàm `push()` của mảng `window.scopeDataLayer`.
  - Giữ nguyên các thuộc tính lồng nhau như `items[]`; chỉ chuyển các event có khai báo trong `eventMap` và chỉ gắn khi đã có đồng ý (`consent`).
- **Chốt phương án với SCOPE (07/10):**
  - `scopeDataLayer` đổi sang mảng, trang push `page_context`, event gửi qua `scopeTrack.event()`; `eventMap` dùng chung tại `scope-contracts/mappings/leocdp_event_map.json` (S1 0.8 FR-01).
- **Mã nguồn & Tài liệu liên quan:**
  - Code: [`leo.proxy.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.proxy.js), [`test_web_sdk_consent_and_datalayer.js`](../../customer360-event-api/tests/test_web_sdk_consent_and_datalayer.js).

---

## Q06

**Trạng thái:** Đã trả lời · **Hạn:** 16/10/2026 · **Đặc tả:** [S1](https://claude.ai/artifact/NuEsMpQVLm9JqCiGRmWBfj) FR-03  
**Ảnh hưởng tới:** S1, S2 nghiệm thu  

### Câu hỏi

Khi người dùng từ chối, tag có còn gửi request nào (ví dụ lấy cấu hình) không? Request đó có cần thiết về kỹ thuật không?

### Câu trả lời

- **Phản hồi của Team CDP (07/10):**
  - Nếu trang không tải `leo.proxy.js` thì hoàn toàn không có request nào.
  - Nếu đặt `window.leoTrackingConsent = false` trước khi tải thì SDK không tạo iframe và không gửi bất kỳ request tracking nào.
  - Khi người dùng rút lại sự đồng ý sau khi SDK đã chạy: gọi `setConsent(false)` sẽ xoá hàng đợi, xoá `localStorage` cache và gỡ iframe (chờ xác nhận 250 ms). Tracking API không kiểm tra consent ở phía server.
- **Chốt phương án với SCOPE (07/10):**
  - Chỉ tải LeoCDP khi `analytics = true`, không dùng mặc định cho phép; loader S5 lọc thêm theo consent (S0 §7, S1 FR-03, S10 FR-14).
- **Mã nguồn & Tài liệu liên quan:**
  - Code: [`leo.proxy.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.proxy.js), [`cdp-event-proxy.html`](../../customer360-event-api/static/c360-web-sdk/html/cdp-event-proxy.html), [`test_web_sdk_consent_and_datalayer.js`](../../customer360-event-api/tests/test_web_sdk_consent_and_datalayer.js).

---

## Q40

**Trạng thái:** Đã trả lời · **Hạn:** 23/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §12.1  
**Ảnh hưởng tới:** Chính sách lưu  
**Cập nhật:** 21/10/2026  

### Câu hỏi

LeoCDP có tự xoá profile không hoạt động theo một thời hạn cấu hình được (đề xuất mặc định 395 ngày, profile_inactive ở S0 §12.1) không?

### Câu trả lời

- **Phản hồi của Team CDP:** **Có.** Hệ thống hỗ trợ theo dõi ngày hoạt động cuối cùng (`last_activity_at`) và trạng thái hồ sơ (`status_code`: `1` = active, `0` = inactive, `-1` = deleted) trên `cdp_master_profiles` để dọn dẹp/ẩn các hồ sơ không hoạt động theo ngưỡng cấu hình (ví dụ 395 ngày).
- **Mã nguồn liên quan:** [`identity.py`](../../customer360-dao/src/leo_customer360_dao/models/identity.py), [`database-schema.sql`](../../customer360-database/database-schema.sql).

---

## Q57

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §1.2, I8, S5 FR-12  
**Ảnh hưởng tới:** S5 FR-12 (nối chéo site), S9 profile cache, S13 gộp người nhận, PPID theo profile  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Mục tiêu: SCOPE và LeoCDP cùng nhận ra một người là một người. Nếu không, mỗi bên giữ một danh sách riêng và không ghép được. Nhờ team LeoCDP 3 việc:

(1) Dùng chung mã khách truy cập. Khi người dùng vào voh.com.vn, scope.js tạo mã, ví dụ v_3kQ9xLmP0aZ7bC2dE4fG6h ("v_" + 22 ký tự chữ và số), và đặt vào window.injectedVisitorId trước khi tải leo.proxy.js. Nhờ xác nhận LeoCDP giữ NGUYÊN mã này làm anonymous_id (không đổi định dạng, không băm lại, không từ chối vì khác dạng 32 hex). Nếu bị đổi, event trong kho dữ liệu của SCOPE mang mã v_… còn LeoCDP lưu mã khác, hai bên không khớp.

(2) Mỗi giờ cung cấp danh sách "mã nào thuộc về người nào". LeoCDP là nơi biết hai mã là cùng một người (ví dụ đăng nhập cùng email ở hai site). Ví dụ:
  visitor_id | site | master_profile_id | email_sha256 | phone_sha256 | user_id | linked_ts | updated_ts
  v_3kQ9… | voh | 8b1f6a2e-… | 5e88…(băm email) | | | 2026-11-10 08:15 | 2026-11-11 01:00
  v_9TnR… | mgcr | 8b1f6a2e-… | 5e88… | 9c1d…(băm SĐT) | mgcr:10293 | 2026-11-11 00:40 | 2026-11-11 01:00
Cách giao: API hoặc file trên S3/MinIO, chỉ cần các dòng thay đổi kể từ lần trước (theo updated_ts). SCOPE dùng để: gộp hành vi nhiều site vào một người; gửi 1 thông báo deal cho người đăng ký push ở nhiều site; gắn PPID theo người. LeoCDP không báo khi gộp hai profile (Q03), nên SCOPE so danh sách giờ này với giờ trước: mọi mã của profile B nay nằm dưới profile A thì coi là B đã gộp vào A.

(3) Nối event website vào bước dựng profile. Theo câu trả lời Q01 (TODO 3), event từ Web SDK vào được LeoCDP nhưng chưa được đưa sang raw profile staging, nên identity resolution chưa chạy trên event của SCOPE. Giống hộp thư nhận đủ thư nhưng chưa ai phân thư vào hồ sơ từng người. Ví dụ: khi người dùng đăng nhập, trang gửi event identify kèm email_sha256; nhờ LeoCDP dùng event này để nối mã v_… vào đúng profile.

Thứ tự: (1) là nền; (3) giúp LeoCDP nối được profile từ dữ liệu website; (2) đưa kết quả nối sang SCOPE. Thiếu (3) thì danh sách ở (2) gần như trống. Chưa có cả 3: SCOPE vẫn chạy nhưng coi mỗi mã là một người riêng, mất phần nối chéo site.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **(1) `anonymous_id`:** LeoCDP lưu nguyên `window.injectedVisitorId`; validate nhận cả `^v_[0-9A-Za-z]{22}$` lẫn 32 hex; không băm, không đổi hoa/thường. Dự phòng nếu SDK buộc 32 hex: giữ `scope.vid` trong envelope làm định danh phụ; bảng ở (2) vẫn xuất theo `v_…`.
> - **(3) Dựng profile từ event website:** Chỉ cần đưa event có định danh (`identify`, `login`, `signup`, đăng ký nhận tin) vào profile staging. Khoá nối: `email_sha256`, `phone_sha256`, `user_id` có tiền tố site (`mgcr:10293`). Chuẩn hoá trước khi băm: email viết thường + bỏ khoảng trắng; SĐT E.164 (`84…`).
> - **(2) Bảng `visitor → profile`:** Xuất file trên S3/MinIO mỗi giờ (`dt=/hour=`), schema `batch/cdp_identity_link.row`. Khi một profile đổi thì gửi lại mọi `visitor_id` của profile đó (nên thêm cột `prev_master_profile_id`). SLA: có file trong ≤ 15 phút sau mỗi giờ, giữ ≥ 7 ngày, mỗi tuần 1 bản đầy đủ.

#### 1. Tóm tắt trạng thái đáp ứng

| Hạng mục | Trạng thái | Ghi chú thực tế trong LeoCDP |
|---|---|---|
| **(1) Giữ nguyên `window.injectedVisitorId`** | **Đồng ý (Đã có sẵn)** | Web SDK, Tracking API và S3 JSONL đều giữ nguyên chuỗi `v_...`, không băm lại hay ép về 32 hex. |
| **(3) Nối event website vào Profile Staging** | **Đã có luồng (Cần chỉnh nhỏ)** | Đã có job đọc S3 → `cdp_raw_profiles_stage` → CIR; chỉ cần map thêm `user_id` sang `external_customer_id`. |
| **(2) Xuất file `cdp_identity_link.row` mỗi giờ** | **Cần phát triển thêm** | Đã có bảng `cdp_profile_links` và REST API tra cứu; cần viết thêm Dagster job xuất file delta ra S3 mỗi giờ. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Mục (1) — Giữ nguyên `anonymous_id` (`window.injectedVisitorId`):**
  - **Web SDK:** Nhận `window.injectedVisitorId` (> 5 ký tự) tại [`leo.proxy.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.proxy.js) (`createProxyIframe`), truyền qua hash vào [`cdp-event-proxy.html`](../../customer360-event-api/static/c360-web-sdk/html/cdp-event-proxy.html) (`INJECTED_VISITOR_ID`) và ưu tiên trả về nguyên bản tại [`leo.observer.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.observer.js) (`generateAnonymousId`, `getAnonymousId`).
  - **Tracking API & S3:** [`schemas.py`](../../customer360-event-api/core/schemas.py) (`normalize_identifier`, `TrackingLogRequest`) chỉ `.strip()` và giới hạn `<= 256` ký tự. Hàm [`build_tracking_object`](../../customer360-event-api/core/storage.py) lưu nguyên `anonymous_id` vào cả `identity.anonymous_id` và `payload` trong file `.jsonl.gz`.
- **Mục (3) — Dựng profile từ event website (S3 → Raw Profile Staging → CIR):**
  - **Luồng hiện tại:** Dagster job [`TrackingLogAggregationService`](../../customer360-backend/analytics/source_analytics/tracking_log_service.py) đọc các file JSONL từ S3/MinIO, qua [`EventRecordService.normalize_event_record`](../../customer360-backend/analytics/source_analytics/event_records.py) và [`extract_raw_profile`](../../customer360-backend/analytics/source_analytics/event_records.py) để `upsert_raw_profile` vào `cdp_raw_profiles_stage` ([`identity_repository.py`](../../customer360-dao/src/leo_customer360_dao/repositories/identity_repository.py)), sau đó [`CustomerIdentityResolver`](../../customer360-backend/identity_resolution/identity_resolution/resolver.py) nối vào `cdp_master_profiles`.
  - **Điểm cần điều chỉnh:** Trong `normalize_event_record`, cần ánh xạ `user_id` (tiền tố site như `mgcr:10293`) vào `external_customer_id` và bỏ fallback `or anonymous_id`.
- **Mục (2) — Xuất bảng nối `visitor → profile`:**
  - **Đã có:** Bảng [`cdp_profile_links`](../../customer360-dao/src/leo_customer360_dao/models/identity.py), API [`GET /api/v1/profile-links`](../../customer360-api/core/routers/identity_api.py), [`GET /api/v1/master-profiles?anonymous_id=...`](../../customer360-api/core/routers/identity_api.py) và [`MasterProfileEventProjector`](../../customer360-backend/identity_resolution/identity_resolution/profile_event_projection.py).
  - **Cần thêm:** 1 Dagster job xuất định kỳ hàng giờ ra S3 theo schema `batch/cdp_identity_link.row`.
- **Tài liệu:** [`customer360-event-api/README.md`](../../customer360-event-api/README.md), [`1-web-sdk-tracking.md`](../data-sources/1-web-sdk-tracking.md), [`customer360-backend/analytics/README.md`](../../customer360-backend/analytics/README.md), [`customer360-backend/identity_resolution/README.md`](../../customer360-backend/identity_resolution/README.md).

---

## Q58

**Trạng thái:** Còn mở · **Hạn:** 06/11/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §5, I3  
**Ảnh hưởng tới:** B4 tracking lên voh và xosodaiphat (tuần T6–T7)  
**Cập nhật:** 21/10/2026  

### Câu hỏi

(1) Đo tải bền (không phải tăng dần) với event thật ở mức 3 lần đỉnh giờ quay của xosodaiphat, nhiều replica, Redis và S3/MinIO giống production; báo số request/giây, event/giây, độ trễ, tỷ lệ lỗi, độ sâu hàng đợi. (2) Giới hạn mặc định 120 request/IP/60 giây sẽ chặn nhầm người dùng mạng di động dùng chung IP: bỏ giới hạn theo IP ở gateway hoặc nâng lên mức nào? (3) Cảnh báo khi hàng đợi đầy (API từ chối lô) gửi cho ai.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **(1)** Chạy đo tải bền ≥ 60 phút ở mức 3 lần đỉnh giờ quay `xosodaiphat` trên staging giống production; báo RPS, EPS, p95/p99, tỷ lệ lỗi, độ sâu hàng đợi trước 06/11.
> - **(2)** Bỏ giới hạn theo IP cho Tracking API, thay bằng giới hạn theo site key và kích thước lô; nếu bắt buộc giữ thì nâng lên ≥ 1.200 request/IP/60 giây.
> - **(3)** Cảnh báo hàng đợi đầy gửi cho Team CDP và Data Engineer của SCOPE.

#### 1. Tóm tắt trạng thái đáp ứng

| Hạng mục | Trạng thái | Ghi chú thực tế trong LeoCDP |
|---|---|---|
| **(1) Đo tải bền ≥ 60 phút (3× đỉnh)** | **Đã có công cụ & baseline** | Đã có script bắn tải và báo cáo UAT 3 replica (~105.2 RPS, 99.98% thành công); sẵn sàng chạy soak test 60 phút khi nhận mẫu từ SCOPE. |
| **(2) Nâng / bỏ Rate Limit theo IP** | **Đồng ý (Hỗ trợ sẵn)** | Cấu hình ngay qua biến môi trường `TRACKING_RATE_LIMIT_REQUESTS` (ví dụ `>= 1200`) hoặc bỏ `client_ip` khỏi khoá rate limit. |
| **(3) Cảnh báo khi hàng đợi đầy** | **Đã có Metrics & Health API** | Xuất trực tiếp qua `/api/v1/tracking/queue-status` và Prometheus `/metrics` để bắn alert sang Team CDP & SCOPE. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Mục (1) — Công cụ đo tải & Kết quả baseline UAT:**
  - Script bắn tải: [`perf_uat_tracking.py`](../../customer360-event-api/tests/perf_uat_tracking.py).
  - Báo cáo thực nghiệm: [`PERF_REPORT.md`](../../customer360-event-api/tests/reports/PERF_REPORT.md) (10.000 events trên 3 replica, đạt ~105.2 RPS bền vững, 100% request được nhận đều ghi thành công xuống S3).
- **Mục (2) — Cấu hình Rate Limit & Giới hạn kích thước lô:**
  - **Rate Limit:** Định nghĩa trong [`config.py`](../../customer360-event-api/core/config.py) và [`redis_cache.py`](../../customer360-event-api/core/redis_cache.py) (`build_rate_limit_key`, `TrackingRequestProtection.allow_request`) theo khoá `ip + data_source_id + origin` (`TRACKING_RATE_LIMIT_REQUESTS=120`, `TRACKING_RATE_LIMIT_WINDOW_SECONDS=60`, miễn trừ qua `TRACKING_RATE_LIMIT_WHITELIST`). Có thể đặt ngay `TRACKING_RATE_LIMIT_REQUESTS=1200` (hoặc cao hơn) trên môi trường triển khai.
  - **Giới hạn kích thước lô:** Đã áp dụng tại [`request_limits.py`](../../customer360-event-api/core/request_limits.py) (`TRACKING_MAX_REQUEST_BODY_BYTES = 4MB`) và [`tracking.py`](../../customer360-event-api/core/routers/tracking.py) (`TRACKING_MAX_EVENTS_PER_REQUEST = 1000`).
- **Mục (3) — Giám sát & Cảnh báo hàng đợi đầy:**
  - Khi Redis Stream chạm ngưỡng `TRACKING_STREAM_MAX_LENGTH = 100000` trong [`redis_queue.py`](../../customer360-event-api/core/redis_queue.py) (`RedisStreamTrackingStorage`), API trả HTTP `503 Service Unavailable` kèm `Retry-After: 1` và tăng bộ đếm `tracking_ingestion_errors_total`.
  - Endpoint giám sát: [`GET /api/v1/tracking/queue-status`](../../customer360-event-api/core/routers/tracking.py) và Prometheus [`GET /metrics`](../../customer360-event-api/core/metrics.py) (`tracking_queue_depth`, `tracking_queue_capacity`, `tracking_queue_oldest_pending_age_seconds`, `tracking_s3_upload_failures_total`).
- **Tài liệu:** [`PERF_REPORT.md`](../../customer360-event-api/tests/reports/PERF_REPORT.md), [`customer360-event-api/README.md`](../../customer360-event-api/README.md).

---

## Q59

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S5](https://claude.ai/artifact/C5s9148f1U1sYhvHwmuK2F) FR-12, I29  
**Ảnh hưởng tới:** Vai trò LeoCDP làm trung tâm định danh ở GĐ1; S5 FR-12; gộp người nhận S13  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Hợp nhất profile: LeoCDP gộp profile theo những khoá nào (email, SĐT, user_id, Zalo ID, mã click quảng cáo)? Quy tắc gộp và tách, nguồn nào được ưu tiên khi trùng? Gộp chéo site (xosodaiphat, voh, luatvietnam, muagicungre) có chạy không? Gửi tài liệu hoặc màn hình cấu hình.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **Khoá gộp:** `user_id` có tiền tố site, `email_sha256`, `phone_sha256` (E.164), `zalo_uid`. Không gộp theo mã click quảng cáo, `visitor_id` hay vân tay thiết bị.
> - **Ưu tiên khi trùng:** `user_id` đăng nhập > `email` > `SĐT` > `zalo_uid`; thuộc tính lấy giá trị mới nhất.
> - **Gộp chéo site:** Bật cho các site nội bộ Nam Việt (`xosodaiphat`, `voh`, `luatvietnam`, `muagicungre`, `muagiodau`); không gộp site đối tác.
> - **Tách profile:** Có thao tác tách thủ công khi gộp sai, ghi log và báo qua bảng nối (Q57).

#### 1. Tóm tắt trạng thái đáp ứng

| Hạng mục | Trạng thái | Ghi chú thực tế trong LeoCDP |
|---|---|---|
| **Khoá gộp theo Metadata** | **Đồng ý (Cấu hình được)** | Đọc động từ `cdp_profile_attributes`; có thể bật chỉ `email`, `phone_number`, `external_customer_id` và tắt `device_id`, `advertising_id`, `cookie_id`. |
| **Quy tắc ưu tiên khi trùng (Survivorship)** | **Đồng ý (Đã có sẵn)** | Hỗ trợ `most_recent`, `source_priority`, `verified_first`, `verified_then_most_recent`, `overwrite`, `non_null`. |
| **Gộp chéo site nội bộ & tách đối tác** | **Đồng ý (Đã có sẵn)** | Gộp tự động trong cùng `(tenant_id, domain)`; site đối tác tách `tenant_id` được cô lập cứng bằng PostgreSQL RLS. |
| **Tách profile (Unmerge) & Audit Log** | **Đã có Schema & API** | Bảng `cdp_profile_merge_history` lưu snapshot JSONB 2 phía và `cdp_profile_links` quản lý trạng thái `UNLINKED` / `SUPERSEDED`. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Cơ chế khoá gộp (Metadata-driven CIR):**
  - [`CustomerIdentityResolver._get_active_rules`](../../customer360-backend/identity_resolution/identity_resolution/resolver.py) đọc các thuộc tính có `is_identity_resolution = TRUE` và `status = 'ACTIVE'` từ bảng `cdp_profile_attributes` (khởi tạo tại [`init-core-database.sql`](../../customer360-database/init-core-database.sql)).
  - Hỗ trợ khớp chính xác (`exact`) trên `email`, `phone_number` (kể cả giá trị băm SHA-256 kèm cờ `is_hashed = TRUE`) và `external_customer_id` (lưu vào JSONB `external_ids` theo từng nguồn `source_system`, dùng cho `user_id` tiền tố site và `zalo_uid`).
  - Để không gộp theo mã quảng cáo hay cookie/thiết bị như SCOPE yêu cầu, chỉ cần cập nhật `is_identity_resolution = FALSE` cho `device_id`, `advertising_id`, `cookie_id` trong `cdp_profile_attributes`.
- **Quy tắc ưu tiên khi trùng (Consolidation Rules):**
  - Thực thi tại [`resolver.py`](../../customer360-backend/identity_resolution/identity_resolution/resolver.py) (`_resolve_scalar_consolidation`) theo cấu hình `consolidation_rule` và `consolidation_config`.
- **Gộp chéo site & Cô lập đối tác:**
  - [`_find_master_profile`](../../customer360-backend/identity_resolution/identity_resolution/resolver.py) khớp trong phạm vi `(tenant_id, domain)`. Các site thuộc Nam Việt dùng chung `tenant_id` sẽ tự động gộp chéo site; site đối tác khác `tenant_id` được cô lập bởi Row-Level Security ([database schema](../../customer360-database/database-schema.sql)).
- **Tách profile & Nhật ký kiểm toán:**
  - Bảng [`cdp_profile_merge_history`](../../customer360-database/database-schema.sql) lưu `source_profile_snapshot` & `target_profile_snapshot` (JSONB); bảng [`cdp_profile_links`](../../customer360-dao/src/leo_customer360_dao/models/identity.py) hỗ trợ trạng thái `ACTIVE | HISTORICAL | UNLINKED | SUPERSEDED` kèm `unlinked_at`, `unlinked_reason`, `unlinked_by`, quản lý qua [`identity_api.py`](../../customer360-api/core/routers/identity_api.py).
- **Tài liệu:** [`CIR-Tech-Slides-VN.md`](../identity-resolution/CIR-Tech-Slides-VN.md), [`CIR-improvement.md`](../identity-resolution/CIR-improvement.md), [`customer360-backend/identity_resolution/README.md`](../../customer360-backend/identity_resolution/README.md), [`attributes.md`](../pages/attributes.md).

---

## Q60

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §2.3  
**Ảnh hưởng tới:** Phạm vi profile 360 GĐ1; quyết định SCOPE có phải xây khối nạp nguồn ngoài hay không  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Nạp nguồn ngoài website: LeoCDP có sẵn kết nối Fanpage Facebook, Zalo OA, YouTube, TikTok, tài khoản Meta/Google/TikTok Ads, file khách hàng (CSV/API từ luatvietnam, muagicungre) không? Với mỗi nguồn: lấy được dữ liệu gì, theo từng người hay tổng hợp, bao lâu cập nhật một lần?

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **GĐ1 chỉ cần:** (1) File khách hàng CSV/API từ `luatvietnam`, `muagicungre` — theo từng người, hằng ngày; (2) Zalo OA người quan tâm — theo từng người, khi có đăng nhập Zalo (Q70).
> - **Các nguồn mạng xã hội & Ads (Fanpage, YouTube, TikTok, Meta/Google/TikTok Ads):** Chỉ số tổng hợp, không vào profile; nguồn nào chưa có sẵn thì không xây ở GĐ1.

#### 1. Bảng trạng thái các nguồn kết nối trong LeoCDP

| Nguồn dữ liệu | Có sẵn trong Repo? | Cấp độ dữ liệu | Tần suất / Cơ chế |
|---|---|---|---|
| **API / File CSV khách hàng (`luatvietnam`, `muagicungre`)** | **Có sẵn hạ tầng API & S3** | Theo từng người (Raw Profile / Event) | Realtime qua REST API hoặc Batch hằng ngày qua S3/MinIO |
| **Zalo OA (OAuth, Webhook & Opt-out)** | **Có sẵn** | Theo từng người | Realtime qua Webhook (`POST /api/v1/track/zalo/webhook`) & Scheduled projection |
| **Meta / Google / TikTok Ads** | **Chỉ có chiều Outbound** | Đẩy tệp Audience (SHA-256) | Theo chiến dịch / Dagster job (`data_synch_job`); chưa có chiều kéo báo cáo Inbound |
| **Facebook Fanpage, YouTube, TikTok Organic** | **Chưa có worker Inbound** | Chỉ có schema cấu hình `sys_data_source` | Không xây ở GĐ1 (đúng theo chốt của SCOPE) |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **(1) Nạp dữ liệu khách hàng từ `luatvietnam`, `muagicungre`:**
  - **Qua REST API:** Gửi trực tiếp vào [`POST /api/v1/raw-profiles`](../../customer360-api/core/routers/identity_api.py) (`create_raw_profile`) hoặc [`POST /api/v1/tracking/logs`](../../customer360-event-api/core/routers/tracking.py).
  - **Qua File S3/MinIO:** Đã có hàm [`IdentityRepository.upsert_raw_profile`](../../customer360-dao/src/leo_customer360_dao/repositories/identity_repository.py) và mẫu batch importer đọc file từ S3 tại [`content_import.py`](../../customer360-backend/data_synch/content_import.py), [`product_import.py`](../../customer360-backend/data_synch/product_import.py).
- **(2) Kết nối Zalo OA:**
  - Quản lý OAuth v4 & làm mới token tự động tại [`zalo_repository.py`](../../customer360-api/core/repositories/zalo_repository.py), [`auth_api.py`](../../customer360-api/core/routers/auth_api.py) (`GET /api/v1/auth/zalo-redirect`) và [`token_refresh.py`](../../customer360-backend/notification_engine/notification_engine/token_refresh.py).
  - Nhận webhook sự kiện theo từng người tại [`zalo_tracking.py`](../../customer360-event-api/core/routers/zalo_tracking.py) và cập nhật trạng thái đồng ý tại [`optout_projection.py`](../../customer360-backend/notification_engine/notification_engine/optout_projection.py).
- **(3) Quản lý danh mục Data Sources:**
  - Model [`SysDataSource`](../../customer360-dao/src/leo_customer360_dao/models/system.py) và CRUD API [`data_source_api.py`](../../customer360-api/core/routers/data_source_api.py).
- **Tài liệu:** [`data-source-types.md`](../data-sources/data-source-types.md), [`2-data-connector.md`](../data-sources/2-data-connector.md), [`3-web-hook-api.md`](../data-sources/3-web-hook-api.md), [`4-s3-files-synch.md`](../data-sources/4-s3-files-synch.md), [`datasources.md`](../pages/datasources.md).

---

## Q61

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S5](https://claude.ai/artifact/C5s9148f1U1sYhvHwmuK2F) FR-17, I29  
**Ảnh hưởng tới:** Chuyển nơi định nghĩa segment sang LeoCDP; S5 FR-17 thành dự phòng  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Tạo segment: marketing tự tạo segment trên giao diện LeoCDP được không (điều kiện theo hành vi, thuộc tính, nguồn)? Có xem trước quy mô tệp, lịch cập nhật segment (thời gian thực, mỗi giờ, mỗi ngày) không? Có phân quyền người tạo, người duyệt không?

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - Marketing tự tạo segment trên giao diện LeoCDP: điều kiện theo hành vi, thuộc tính (kể cả thuộc tính SCOPE gửi sang ở Q65), nguồn; xem trước quy mô tệp; cập nhật segment mỗi giờ; phân quyền người tạo và người duyệt.
> - Nếu LeoCDP chưa đủ các điểm trên thì S5 FR-17 vẫn là nơi chính định nghĩa segment ở GĐ1.

#### 1. Tóm tắt trạng thái đáp ứng

| Tính năng yêu cầu | Trạng thái | Ghi chú thực tế trong LeoCDP |
|---|---|---|
| **Tạo segment theo thuộc tính, nguồn & custom fields** | **Đã có sẵn** | Giao diện QueryBuilder trực quan + tạo bằng ngôn ngữ tự nhiên (AI Draft). |
| **Xem trước quy mô tệp & danh sách khớp** | **Đã có sẵn** | Có API đếm nhanh số lượng (`member_count`) và xem danh sách profile khớp. |
| **Lịch cập nhật tự động (≤ mỗi giờ)** | **Đã có sẵn (60 giây/lần)** | Dagster sensor kiểm tra mỗi 60s và tự động tính lại khi `cdp_master_profiles` có thay đổi. |
| **Phân quyền người tạo / người duyệt** | **Có ở Campaign/Workflow** | Segment hiện kích hoạt theo `is_active`; quy trình duyệt 2 bước (`Draft → InReview → Approved`) đang đặt ở bước **Campaign & Workflow** sử dụng segment đó. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Giao diện tạo Segment (Audience Builder & AI):**
  - Giao diện frontend: [`segments-view.js`](../../customer360-frontend/static/js/segments-view.js), [`segment.querybuilder.js`](../../customer360-frontend/static/js/segment.querybuilder.js).
  - API CRUD & AI sinh quy tắc: [`segment_api.py`](../../customer360-api/core/routers/segment_api.py) (`/api/v1/segments`, `POST /api/v1/segments/from-description`), [`segment_draft_repository.py`](../../customer360-api/core/repositories/segment_draft_repository.py).
  - Danh mục trường lọc động: [`GET /api/v1/segments/segmentable-profile-attributes`](../../customer360-api/core/routers/segment_api.py) tự động lấy mọi trường có `is_segmentable = TRUE` từ `cdp_master_profiles` và `cdp_domain_profiles.domain_attributes`.
- **Xem quy mô tệp & danh sách khách hàng khớp:**
  - [`GET /api/v1/segments/{segment_id}/matched-profiles/count`](../../customer360-api/core/routers/segment_api.py) và [`GET /api/v1/segments/{segment_id}/matched-profiles`](../../customer360-api/core/routers/segment_api.py) (kiểm tra an toàn SQL qua [`sql_safety.py`](../../customer360-dao/src/leo_customer360_dao/utils/sql_safety.py)).
- **Lịch cập nhật tự động & theo yêu cầu:**
  - Tự động kích hoạt khi tạo/sửa segment, khi gọi `POST /api/v1/segments/{segment_id}/recompute`, hoặc tự động qua [`segmentation_poll_sensor`](../../customer360-backend/segmentation/dagster_defs.py) (`SEGMENTATION_POLL_INTERVAL_SECONDS=60`) thực thi [`recompute.py`](../../customer360-backend/segmentation/segmentation/recompute.py).
- **Phân quyền & Phê duyệt:**
  - Quản lý vai trò RBAC tại [`user_api.py`](../../customer360-api/core/routers/user_api.py); kiểm tra quyền phê duyệt tách biệt người tạo / người duyệt tại [`campaign_draft_repository.py`](../../customer360-api/core/repositories/campaign_draft_repository.py) và [`campaign_workflow_repository.py`](../../customer360-api/core/repositories/campaign_workflow_repository.py).
- **Tài liệu:** [`segments.md`](../pages/segments.md), [`segment-detail.md`](../pages/segment-detail.md), [`customer360-backend/segmentation/README.md`](../../customer360-backend/segmentation/README.md), [`PLAN-SEGMENTS-API-IMPROVEMENT.md`](../action-plans/PLAN-SEGMENTS-API-IMPROVEMENT.md).

---

## Q62

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) I13, I29  
**Ảnh hưởng tới:** Kích hoạt ra kênh ngoài ở GĐ1 do LeoCDP hay SCOPE làm; consent marketing  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Kích hoạt ra ngoài: LeoCDP có đẩy tệp khách hàng lên Meta Custom Audience, Google Customer Match, TikTok Audience không? Có gửi Zalo ZNS, email, SMS không, qua nhà cung cấp nào? Có lọc theo consent marketing của SCOPE (S2) trước khi gửi không?

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - LeoCDP đẩy tệp lên Meta Custom Audience, Google Customer Match, TikTok Audience; gửi ZNS, email, SMS qua nhà cung cấp hiện có.
> - Bắt buộc lọc theo `cdp_consent` trước khi gửi: đẩy tệp cần `ads_offsite`; ZNS/email/SMS cần `marketing` (S0 §7.4). Không lọc được thì SCOPE chặn ở `cdp-bridge` (Q85).

#### 1. Bảng trạng thái các kênh kích hoạt & lọc Consent

| Kênh kích hoạt | Nhà cung cấp / Giao thức hỗ trợ | Trạng thái Code | Cơ chế lọc Consent & Suppression hiện tại |
|---|---|---|---|
| **Meta Custom Audience** | Facebook Graph API (`v21.0`) | **Đã có sẵn** | Lọc `status_code = 1` + `crm_suppression_list` (cần thêm cờ `ads_offsite`, xem [Q85](#q85)) |
| **Google Customer Match** | Google Ads `offlineUserDataJobs` (`v19`) | **Đã có sẵn** | Lọc `status_code = 1` + `crm_suppression_list` (cần thêm cờ `ads_offsite`, xem [Q85](#q85)) |
| **TikTok Audience** | TikTok Marketing API (`dmp/custom_audience`) | **Đã có sẵn** | Lọc `status_code = 1` + `crm_suppression_list` (cần thêm cờ `ads_offsite`, xem [Q85](#q85)) |
| **Zalo ZNS** | Zalo Business OpenAPI (`business.openapi.zalo.me`) | **Đã có sẵn** | Kiểm tra `communication_preferences.zalo_opt_in != false` |
| **Email** | SMTP / Tenant Email Connector (`crm_connector_config`) | **Đã có sẵn** | Kiểm tra `email_opt_in` + bảng `crm_suppression_list` |
| **SMS & Push** | HTTP Webhook SMS / OneSignal / Mobile Native | **Đã có sẵn** | Kiểm tra `sms_opt_in == true` / `push_opt_in == true` trong Workflow |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Đẩy tệp quảng cáo (Meta, Google, TikTok):**
  - Triển khai tại [`audience_sync.py`](../../customer360-backend/data_synch/audience_sync.py) (`MetaAudienceProvider`, `GoogleAdsAudienceProvider`, `TikTokAudienceProvider`) và Dagster job [`dagster_defs.py`](../../customer360-backend/data_synch/dagster_defs.py). Tự động chuẩn hoá và băm SHA-256 email/SĐT trong bộ nhớ trước khi gửi, lưu audit vào `crm_ad_sync_runs`.
- **Gửi Zalo ZNS, Email, SMS, Push:**
  - **Zalo ZNS:** [`notification_engine/send.py`](../../customer360-backend/notification_engine/notification_engine/send.py) và [`notification_engine/adapters.py`](../../customer360-backend/notification_engine/notification_engine/adapters.py).
  - **Email:** [`email_engine/send.py`](../../customer360-backend/email_engine/email_engine/send.py) và [`email_engine/adapters.py`](../../customer360-backend/email_engine/email_engine/adapters.py).
  - **SMS & Push (Campaign Workflow):** [`workflow_runner.py`](../../customer360-backend/campaign_activation/campaign_activation/workflow_runner.py) và [`workflow/nodes.py`](../../customer360-dao/src/leo_customer360_dao/workflow/nodes.py).
  - **Danh sách chặn (Suppression List):** Model [`SuppressionList`](../../customer360-dao/src/leo_customer360_dao/models/crm.py) (`crm_suppression_list`).
- **Tài liệu:** [`customer360-backend/data_synch/README.md`](../../customer360-backend/data_synch/README.md), [`customer360-backend/email_engine/README.md`](../../customer360-backend/email_engine/README.md), [`customer360-backend/notification_engine/README.md`](../../customer360-backend/notification_engine/README.md), [`customer360-backend/campaign_activation/README.md`](../../customer360-backend/campaign_activation/README.md).

---

## Q63

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S5](https://claude.ai/artifact/C5s9148f1U1sYhvHwmuK2F) FR-12, FR-17, I29  
**Ảnh hưởng tới:** Lớp trung gian I29; S9 profile cache; tệp GAM I13  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Lấy dữ liệu ra cho SCOPE: SCOPE đọc profile, bảng nối định danh và danh sách thành viên segment bằng cách nào (REST API, file trên S3/MinIO, webhook)? Có lấy được phần thay đổi theo updated_ts không, bao lâu một lần, giới hạn gọi API bao nhiêu? Gửi tài liệu API mẫu.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **Bảng nối định danh (Q57) và thành viên segment:** Xuất file trên S3/MinIO mỗi giờ, phần thay đổi theo `updated_ts`, schema `batch/cdp_identity_link.row` và `batch/cdp_segment_member.row`.
> - **Profile:** REST API đọc từng profile để tra cứu, giới hạn ≥ 50 request/giây. Không cần webhook.

#### 1. Tóm tắt trạng thái đáp ứng

| Loại dữ liệu | Đọc qua REST API (Đã có sẵn) | Xuất file S3 mỗi giờ (Theo đề xuất) |
|---|---|---|
| **Hồ sơ khách hàng (Profile)** | `GET /api/v1/master-profiles/{id}` & `GET /api/v1/master-profiles` (có Redis cache, đáp ứng ≥ 50 RPS) | Đã có `MasterProfileEventProjector` xuất JSON theo profile |
| **Bảng nối định danh (Identity Links)** | `GET /api/v1/profile-links` & `GET /api/v1/master-profiles/{id}/links` | **Cần viết thêm** Dagster job xuất `batch/cdp_identity_link.row` |
| **Thành viên Segment (Segment Members)** | `GET /api/v1/segments/{segment_id}/matched-profiles` | **Cần viết thêm** Dagster job xuất `batch/cdp_segment_member.row` |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Các REST API hiện có (kèm OpenAPI `/docs`):**
  - **Tra cứu Profile:** [`identity_api.py`](../../customer360-api/core/routers/identity_api.py) (`get_master_profile`, `list_master_profiles` hỗ trợ lọc theo `anonymous_id`, `device_fingerprint`, `from_date`, `to_date`), tăng tốc qua Redis response cache tại [`cache.py`](../../customer360-api/core/cache.py).
  - **Tra cứu Bảng nối định danh:** [`identity_api.py`](../../customer360-api/core/routers/identity_api.py) (`get_master_profile_links`, `list_profile_links`).
  - **Tra cứu Thành viên Segment:** [`segment_api.py`](../../customer360-api/core/routers/segment_api.py) (`get_segment_matched_profiles`).
  - **OpenAPI Spec:** Khởi tạo tại [`http_api_app.py`](../../customer360-api/core/apps/http_api_app.py) (truy cập `/docs` hoặc `/openapi.json`).
- **Luồng xuất file S3/MinIO:**
  - Tham khảo kiến trúc xuất S3 hiện có tại [`MasterProfileEventProjector`](../../customer360-backend/identity_resolution/identity_resolution/profile_event_projection.py) và [`MasterProfileEventStore`](../../customer360-dao/src/leo_customer360_dao/repositories/master_profile_event_repository.py) để bổ sung job xuất delta theo `updated_at`.
- **Tài liệu:** [`customer360-api/README.md`](../../customer360-api/README.md), [`profiles.md`](../pages/profiles.md), [`profile-detail.md`](../pages/profile-detail.md), [`segment-detail.md`](../pages/segment-detail.md).

---

## Q64

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §9.7, I29  
**Ảnh hưởng tới:** Điều kiện để GĐ2 (mua lại hoặc thay thế) không mất dữ liệu; anh Thọ dùng khi đàm phán  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Quyền dữ liệu và lối ra: Nam Việt xuất được toàn bộ dữ liệu (profile, định danh, segment, event) ra định dạng mở bất cứ lúc nào không? Mã nguồn LeoCDP thuộc quyền ai, giấy phép gì? LeoCDP có sẵn sàng bàn chuyển nhượng mã nguồn hoặc cùng phát triển ở GĐ2 không?

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - Nam Việt xuất được toàn bộ profile, định danh, segment, event ra định dạng mở (JSONL, Parquet hoặc CSV) bất cứ lúc nào, trong ≤ 7 ngày từ khi yêu cầu, không tính phí; ghi vào phụ lục hợp đồng (L1).
> - Mã nguồn và giấy phép: nhờ Team CDP cho biết; đề nghị sẵn sàng bàn chuyển nhượng hoặc cùng phát triển ở GĐ2.

#### 1. Tóm tắt trạng thái đáp ứng

| Nội dung | Phản hồi từ Team CDP |
|---|---|
| **Giấy phép & Bản quyền mã nguồn** | Phát hành theo giấy phép nguồn mở **Apache License, Version 2.0** (`Copyright 2020 Trieu Nguyen (trieu@leocdp.com)`), cho phép sử dụng, sửa đổi và phát triển tiếp. |
| **Xuất dữ liệu ra định dạng mở** | **Hỗ trợ đầy đủ:** Event đã lưu sẵn dạng `.jsonl.gz` trên S3/MinIO; Profile, Định danh, Segment nằm trong PostgreSQL 16 chuẩn, xuất ra CSV/JSONL/Parquet bất kỳ lúc nào. |
| **Thoả thuận GĐ2 & Phụ lục L1** | Các cam kết thương mại, SLA ≤ 7 ngày và phương án đồng phát triển / chuyển giao GĐ2 do đại diện hai bên chốt trong hợp đồng. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Giấy phép mã nguồn:** Xem toàn văn tại [`LICENSE`](../../LICENSE) và [`README.md`](../../README.md).
- **Định dạng lưu trữ mở của dữ liệu:**
  - **Behavioral Events:** Lưu trực tiếp dưới dạng `.jsonl.gz` (NDJSON nén gzip) trên S3/MinIO theo cấu trúc `s3://data-tracking-<data_source_id>/events/YYYY-MM-DD-HH/<batch_id>.jsonl.gz` tại [`storage.py`](../../customer360-event-api/core/storage.py).
  - **Profiles, Identity Links & Segments:** Lưu trong các bảng PostgreSQL 16 (`cdp_master_profiles`, `cdp_raw_profiles_stage`, `cdp_profile_links`, `cdp_domain_profiles`, `cdp_segments`) định nghĩa tại [`database-schema.sql`](../../customer360-database/database-schema.sql), hỗ trợ xuất trực tiếp qua `COPY` / script vận hành [`dev-db-backup.sh`](../operations/database/dev-db-backup.sh).
- **Tài liệu:** [`LICENSE`](../../LICENSE), [`customer360-database/README.md`](../../customer360-database/README.md), [`full-schema-diagram.md`](../../customer360-database/full-schema-diagram.md), [`docs/operations/database/README.md`](../operations/database/README.md).

---

## Q65

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S0](https://claude.ai/artifact/Vi4zVHQ1thHme3Xht3vw7S) §2.5, §7.4, §12, I29  
**Ảnh hưởng tới:** Chiều SCOPE → LeoCDP của cdp-bridge (S5 FR-12); marketing tạo segment có dữ liệu mua hàng; kích hoạt ra kênh ngoài đúng đồng ý; xoá ở cả hai nơi  
**Cập nhật:** 21/10/2026  

### Câu hỏi

SCOPE sẽ gửi sang LeoCDP 3 loại dữ liệu. LeoCDP nhận bằng cách nào (API hay file trên S3/MinIO), giới hạn bao nhiêu?
(1) Thuộc tính profile chỉ SCOPE có, hằng ngày trước 06:00. Ví dụ: profile 8b1f6a2e-… | buyer=true | order_count_90d=2 | price_band=mid | top_categories=dien_thoai|tai_nghe | aff_clicks_30d=7 | eng_tier=regular. Marketing cần thấy các trường này trên giao diện LeoCDP để tạo segment như "đã mua điện thoại 90 ngày qua". LeoCDP lưu được trường tuỳ biến vào profile không?
(2) Trạng thái đồng ý theo (profile, site), gửi trong ≤ 15 phút sau khi người dùng đổi lựa chọn. Ví dụ: profile 8b1f6a2e-… | site voh | ads=true | marketing=false. LeoCDP có lọc theo trạng thái này trước khi đẩy tệp lên Meta/Google/TikTok (cần ads) và gửi ZNS/email/SMS (cần marketing) không? Quy tắc: lựa chọn mới nhất trên bất kỳ site nào thắng.
(3) Yêu cầu xoá (request_id, profile_id hoặc visitor_id, site), gửi trong ≤ 1 giờ. LeoCDP xoá và trả xác nhận trong ≤ 72 giờ được không?
Schema mẫu: scope-contracts 1.20.0, schemas/batch/cdp_profile_attr.row, cdp_consent.row, cdp_erasure.row.

Cập nhật 17/10 (S0 v1.44–1.45): yêu cầu xoá có request_id và định danh gồm person_id; mỗi nơi, gồm LeoCDP, xác nhận ≤ 48 giờ, cả chuỗi hoàn tất ≤ 72 giờ (thay cho "≤ 72 giờ" ở mục 3); thuộc tính profile thêm person_id (mã người của SCOPE).

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - Nhận cả 3 loại qua file trên S3/MinIO (`batch/cdp_profile_attr.row`, `cdp_consent.row`, `cdp_erasure.row`), ≥ 1 triệu dòng mỗi file.
> - **(1)** Lưu trường tuỳ biến vào profile (kể cả `person_id`), hiện trên giao diện tạo segment.
> - **(2)** Lọc theo trạng thái đồng ý mới nhất trên bất kỳ site nào trước khi đẩy tệp (`ads_offsite`) và gửi ZNS/email/SMS (`marketing`).
> - **(3)** Xoá và trả xác nhận `erasure_done` ≤ 48 giờ (cả chuỗi hoàn tất ≤ 72 giờ).

#### 1. Tóm tắt trạng thái đáp ứng

| Luồng dữ liệu từ SCOPE | Hỗ trợ ở tầng DB / API hiện tại | Cần bổ sung để chạy Batch S3 (scope-contracts) |
|---|---|---|
| **(1) Thuộc tính tuỳ biến (`cdp_profile_attr.row`)** | **Đã có sẵn:** Lưu vào `domain_attributes` / `attributes`, trigger DB tự động đưa lên giao diện Segment Builder. | Viết thêm 1 Dagster job đọc file `cdp_profile_attr.row` từ S3 và bulk-upsert. |
| **(2) Trạng thái đồng ý (`cdp_consent.row`)** | **Đã có một phần:** Lưu tại `communication_preferences` (JSONB merge ghi đè giá trị mới nhất). | Viết worker đọc `cdp_consent.row` mỗi 15 phút và thêm lọc `ads_offsite` ở `audience_sync.py`. |
| **(3) Yêu cầu xoá (`cdp_erasure.row`)** | **Đã có API xoá đơn lẻ:** `DELETE /api/v1/master-profiles/{id}` và `status_code = -1`. | Viết job xử lý `cdp_erasure.row` theo `person_id`/`visitor_id` và xuất file xác nhận `erasure_done` ≤ 48h. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Mục (1) — Lưu trường tuỳ biến & Tự động hiện trên Segment Builder:**
  - Bảng [`cdp_domain_profiles`](../../customer360-dao/src/leo_customer360_dao/models/identity.py) có cột `domain_attributes JSONB` (và `cdp_master_profiles` có `attributes JSONB`, `external_ids JSONB`).
  - Trong [`database-schema.sql`](../../customer360-database/database-schema.sql), trigger `trg_register_domain_profile_attributes` tự động đăng ký mọi khoá JSONB mới trong `domain_attributes` vào bảng `cdp_profile_attributes` với `is_segmentable = TRUE`.
  - Endpoint [`GET /api/v1/segments/segmentable-profile-attributes`](../../customer360-api/core/routers/segment_api.py) tự động trả các trường này (`dp.domain_attributes->>'<key>'`) lên giao diện tạo segment cho Marketing sử dụng ngay.
- **Mục (2) — Cập nhật & Lọc theo trạng thái đồng ý (`communication_preferences`):**
  - Trong [`resolver.py`](../../customer360-backend/identity_resolution/identity_resolution/resolver.py) (`_extract_communication_preferences`), khi hợp nhất hồ sơ, hệ thống gộp `communication_preferences = COALESCE(communication_preferences, '{}') || incoming` (lựa chọn mới nhất tự động thắng).
- **Mục (3) — Xoá dữ liệu hồ sơ:**
  - API xoá hiện có tại [`identity_api.py`](../../customer360-api/core/routers/identity_api.py) (`delete_master_profile`, `delete_raw_profile`, `delete_profile_link`).
- **Tài liệu:** [`attributes.md`](../pages/attributes.md), [`segments.md`](../pages/segments.md), [`TECHNICAL-DOCUMENTATION.md`](../architecture/TECHNICAL-DOCUMENTATION.md).

---

## Q66

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S5](https://claude.ai/artifact/C5s9148f1U1sYhvHwmuK2F) FR-05, S0 §11.2  
**Ảnh hưởng tới:** Nạp lại event sau sự cố; giám sát luồng file event (S5 FR-05)  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Event website chỉ vào kho dữ liệu của SCOPE qua file JSONL của LeoCDP trên S3/MinIO, nên khi LeoCDP gặp sự cố thì SCOPE cũng thiếu dữ liệu doanh thu và A/B. (1) LeoCDP giữ file JSONL trong bao lâu? SCOPE đề xuất ≥ 7 ngày để nạp lại được. (2) Tên file có chứa thời điểm để SCOPE đọc lại theo khoảng thời gian không? (3) Khi Tracking API từ chối lô hoặc ghi file bị dừng, LeoCDP có cảnh báo không, gửi cho ai? SCOPE sẽ tự cảnh báo khi không có file mới quá 15 phút hoặc số event giảm quá 50% so với cùng giờ tuần trước.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **(1)** Giữ file JSONL ≥ 7 ngày.
> - **(2)** Tên file/thư mục có thời điểm UTC để SCOPE đọc lại theo khoảng thời gian.
> - **(3)** Khi Tracking API từ chối lô hoặc ghi file dừng, cảnh báo gửi Team CDP và Data Engineer của SCOPE.

#### 1. Tóm tắt trạng thái đáp ứng

| Hạng mục | Trạng thái | Ghi chú thực tế trong LeoCDP |
|---|---|---|
| **(1) Lưu file JSONL ≥ 7 ngày** | **Đồng ý (Đã có sẵn)** | Code chỉ ghi file bất biến (immutable), không tự xoá; thời hạn giữ ≥ 7 ngày cấu hình qua S3/MinIO Lifecycle Policy. |
| **(2) Phân vùng thư mục theo giờ UTC** | **Đã có sẵn** | Đường dẫn dạng `s3://data-tracking-<data_source_id>/events/YYYY-MM-DD-HH/<batch_uuid>.jsonl.gz` (theo giờ UTC). |
| **(3) Cảnh báo khi từ chối lô / lỗi ghi S3** | **Đã có Metrics & Healthcheck** | Xuất qua `/health` (HTTP 503 khi S3 lỗi) và Prometheus `/metrics` (`tracking_s3_upload_failures_total`, `tracking_ingestion_errors_total`). |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Mục (1) — Cơ chế lưu trữ bất biến trên S3/MinIO:**
  - [`storage.py`](../../customer360-event-api/core/storage.py) (`S3ObjectStorage`) ghi các batch `.jsonl.gz` kèm state marker `_processed/<object_id>.json` trên S3 và không gọi lệnh xoá. Khoá chống trùng ngắn hạn trên Redis giữ 48 giờ (`TRACKING_IDEMPOTENCY_TTL_SECONDS=172800` trong [`config.py`](../../customer360-event-api/core/config.py)).
- **Mục (2) — Cấu trúc đường dẫn theo giờ UTC & Quét lại (Replay):**
  - Hàm [`build_tracking_object`](../../customer360-event-api/core/storage.py) phân vùng thư mục theo `received_at.strftime("%Y-%m-%d-%H")` (`events/YYYY-MM-DD-HH/`).
  - Bộ đọc [`S3EventStore.iter_hourly_objects`](../../customer360-backend/analytics/source_analytics/object_store.py) hỗ trợ quét lại chính xác từ bất kỳ mốc giờ `start_after` nào.
- **Mục (3) — Healthcheck & Prometheus Metrics:**
  - [`GET /health`](../../customer360-event-api/app.py), [`GET /api/v1/tracking/queue-status`](../../customer360-event-api/core/routers/tracking.py) và [`GET /metrics`](../../customer360-event-api/core/metrics.py).
- **Tài liệu:** [`customer360-event-api/README.md`](../../customer360-event-api/README.md), [`4-s3-files-synch.md`](../data-sources/4-s3-files-synch.md).

---

## Q71

**Trạng thái:** Còn mở · **Hạn:** 13/11/2026 · **Đặc tả:** [S5](https://claude.ai/artifact/C5s9148f1U1sYhvHwmuK2F) FR-18, I29  
**Ảnh hưởng tới:** Gộp profile từ nối chéo site trong LeoCDP (S5 FR-18)  
**Cập nhật:** 21/10/2026  

### Câu hỏi

Khi bật nối chéo site (Q68), SCOPE sẽ biết hai mã khách là một người, ví dụ: visitor v_3kQ9… trên voh và v_9TnR… trên xosodaiphat. LeoCDP có nhận được "gợi ý nối" dạng cặp (visitor_id A, site A, visitor_id B, site B, thời điểm) để gộp hai profile ẩn danh không? Nếu có thì qua API hay file, và LeoCDP gộp ngay hay cần quy tắc riêng?

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - Nhận cặp gợi ý nối `(visitor_id A, site A, visitor_id B, site B, thời điểm)` qua file trên S3/MinIO mỗi giờ.
> - Gộp ngay nếu cả hai profile chưa có định danh mạnh; nếu đã có `email`/`SĐT`/`user_id` khác nhau thì không gộp và ghi log. Chỉ bật khi công tắc `xsite_link` bật.

#### 1. Tóm tắt trạng thái đáp ứng

| Hạng mục | Trạng thái | Ghi chú thực tế trong LeoCDP |
|---|---|---|
| **Kiểm tra xung đột định danh mạnh & Lịch sử gộp** | **Đã có sẵn nền tảng** | Đã có hàm `_find_identity_conflicts` và bảng `cdp_profile_merge_history`, `cdp_identity_index`. |
| **Job đọc file gợi ý nối cặp từ S3 (`xsite_link`)** | **Cần phát triển thêm** | Cần viết thêm 1 Dagster job đọc file gợi ý mỗi giờ và thực hiện master-to-master merge cho 2 profile ẩn danh. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Nền tảng đã có trong mã nguồn:**
  - Hàm kiểm tra xung đột định danh mạnh [`_find_identity_conflicts`](../../customer360-backend/identity_resolution/identity_resolution/resolver.py) trong `CustomerIdentityResolver` đảm bảo không gộp đè khi hai hồ sơ có `email` hoặc `phone_number` khác nhau.
  - Bảng lịch sử gộp [`cdp_profile_merge_history`](../../customer360-database/database-schema.sql) (lưu snapshot JSONB của cả `source_master_profile_id` và `target_master_profile_id`) và bảng chỉ mục tra cứu nhanh [`CdpIdentityIndex`](../../customer360-dao/src/leo_customer360_dao/models/identity.py).
- **Phần cần xây dựng thêm (sau khi Q68 duyệt):**
  - Bổ sung 1 Dagster job trong `customer360-backend/identity_resolution` kiểm tra cờ `xsite_link`, đọc file cặp gợi ý trên S3 mỗi giờ, kiểm tra cả 2 master profile chưa có định danh mạnh xung đột, chuyển `cdp_profile_links` từ profile B sang profile A (`status = 'SUPERSEDED'`) và ghi vào `cdp_profile_merge_history`.
- **Tài liệu:** [`customer360-backend/identity_resolution/README.md`](../../customer360-backend/identity_resolution/README.md), [`CIR-improvement.md`](../identity-resolution/CIR-improvement.md), [`identity-resolution-overmerge.excalidraw`](../code-review/diagrams/identity-resolution-overmerge.excalidraw).

---

## Q72

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:** [S1](https://claude.ai/artifact/NuEsMpQVLm9JqCiGRmWBfj) NFR-01, S10 FR-14  
**Ảnh hưởng tới:** Cổng đo hiệu năng M1 (tuần T5) trước khi đưa thẻ SCOPE lên voh và xosodaiphat (B4)  
**Cập nhật:** 21/10/2026  

### Câu hỏi

SCOPE đặt ngân sách cho phần LeoCDP trên trang: ≤ 30 ms luồng chính, ≤ 40 KB (gzip), ≤ 3 request mỗi lượt tải, đo trên trang bài voh, Android tầm trung, mạng 4G. (1) leo.proxy.js cùng iframe và các script con hiện nặng bao nhiêu (KB), tạo bao nhiêu request mỗi lượt tải, chạy bao lâu? (2) Khi SCOPE đã đưa mã khách qua window.injectedVisitorId, LeoCDP có tắt được FingerprintJS (vân tay thiết bị) không? Phần này thường nặng nhất và không còn cần. (3) SCOPE sẽ tải leo.proxy.js sau sự kiện load khi trình duyệt rảnh; có vấn đề gì không? (4) Nếu đo ở tuần T5 vượt ngân sách, LeoCDP có nhận event phía máy chủ (SCOPE gửi từ cdp-bridge qua Tracking API, giữ nguyên envelope) thay cho thẻ trên trang không?

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - **(1)** Báo số đo `leo.proxy.js` + iframe + script con (KB gzip, số request, thời gian luồng chính) trước T5.
> - **(2)** Tắt FingerprintJS khi có `window.injectedVisitorId`.
> - **(3)** Xác nhận việc tải `leo.proxy.js` sau sự kiện `load` khi trình duyệt rảnh không gây lỗi.
> - **(4)** Nếu đo T5 vượt ngân sách: nhận event phía máy chủ từ `cdp-bridge` qua Tracking API, giữ nguyên envelope.

#### 1. Bảng số đo dung lượng Web SDK & Trạng thái đáp ứng

| Thành phần / Yêu cầu | Số đo / Trạng thái thực tế | Đánh giá so với ngân sách SCOPE |
|---|---|---|
| **1. `leo.proxy.js`** (trên trang chính) | `45.2 KB` raw / **`~8.95 KB` gzip** | Trì hoãn `leoProxyDelay = 300ms` mới tạo iframe, rất nhẹ cho luồng chính |
| **2. `cdp-event-proxy.html`** (iframe ẩn) | `35.5 KB` raw / **`~5.81 KB` gzip** | Chạy cô lập trong cross-origin iframe |
| **3. `leo.observer.js`** (trong iframe) | `54.4 KB` raw / **`~12.45 KB` gzip** | Chạy cô lập trong cross-origin iframe |
| **Tổng dung lượng 3 file SDK nội bộ** | `135.1 KB` raw / **`~27.2 KB` gzip** | **Đạt ngân sách** (`27.2 KB <= 40 KB gzip`, đúng `3 request` khi tắt FingerprintJS) |
| **Mục (2) Tắt FingerprintJS khi có `injectedVisitorId`** | **Cần sửa nhẹ vài dòng JS** | Bỏ tải `fingerprint2.min.js` khi đã có `INJECTED_VISITOR_ID` trong `cdp-event-proxy.html` |
| **Mục (3) Tải `leo.proxy.js` sau `load` / idle** | **Đồng ý (Không vấn đề)** | Có hàng đợi `pendingEvents` lưu tạm và tự flush khi iframe báo `Ready` |
| **Mục (4) Server-side Ingest từ `cdp-bridge`** | **Đồng ý (Đã có sẵn)** | Gửi thẳng batch JSON vào `POST /data/api/v1/tracking/logs` |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Mục (1) & (2) — Cấu trúc tải script và tắt FingerprintJS:**
  - Trong [`cdp-event-proxy.html`](../../customer360-event-api/static/c360-web-sdk/html/cdp-event-proxy.html) (`ensureFingerprintAndLoad` dòng 908 và `initFingerprint` dòng 794), hiện tại iframe vẫn gọi `fingerprint2.min.js` từ `cdnjs.cloudflare.com`. Vì backend [`TrackingLogService`](../../customer360-event-api/core/service.py) đã cho phép `device_fingerprint = None` khi có `anonymous_id`, chỉ cần thêm điều kiện bỏ qua `Fingerprint2` khi `INJECTED_VISITOR_ID` khác rỗng.
- **Mục (3) — Cơ chế hàng đợi khi tải trễ (Lazy / Idle Load):**
  - [`leo.proxy.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.proxy.js) giữ toàn bộ sự kiện trong mảng `pendingEvents` và hỗ trợ đọc lại `window.scopeDataLayer` (được kiểm thử tại [`test_web_sdk_consent_and_datalayer.js`](../../customer360-event-api/tests/test_web_sdk_consent_and_datalayer.js)).
- **Mục (4) — Server-side Ingest:**
  - Endpoint [`POST /api/v1/tracking/logs`](../../customer360-event-api/core/routers/tracking.py) (và `/data/api/v1/tracking/logs`) nhận trực tiếp payload từ `cdp-bridge`.
- **Tài liệu:** [`1-web-sdk-tracking.md`](../data-sources/1-web-sdk-tracking.md), [`3-web-hook-api.md`](../data-sources/3-web-hook-api.md), [`customer360-event-api/README.md`](../../customer360-event-api/README.md).

---

## Q78

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:**  S0 §1.1c; S1 NFR-01; S10 FR-14  
**Ảnh hưởng tới:** CSP/CORS của site, đo hiệu năng M1 (T5), danh mục tên miền  
**Cập nhật:** 21/10/2026  

### Câu hỏi

LeoCDP dùng những tên miền nào trên trang và máy chủ? (1) Nơi phục vụ leo.proxy.js và các script con, iframe; (2) endpoint thu event từ trình duyệt; (3) bucket S3/MinIO chứa file event JSONL mà S5 đọc; (4) có dùng được tên miền riêng của Nam Việt qua CNAME (ví dụ cdp.scope.vn) không? Ví dụ trả lời: script tại obs.leocdp.example, event POST tới obs.leocdp.example/track, file tại s3://leocdp-namviet/events/.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - Liệt kê: (1) tên miền phục vụ `leo.proxy.js`, script con, iframe; (2) endpoint thu event; (3) bucket S3/MinIO chứa JSONL.
> - (4) Đề nghị dùng CNAME dưới `scope.vn` (ví dụ `cdp.scope.vn` cho cả script và endpoint) để CSP gọn và giảm bị adblock chặn; SCOPE cấp SSL.

#### 1. Bảng danh mục Tên miền, Đường dẫn (Paths) & Bucket S3

| Hạng mục | Giá trị mặc định (UAT) | Giá trị khi dùng CNAME `cdp.scope.vn` |
|---|---|---|
| **(1a) Script chính (`leo.proxy.js`)** | `https://beta.leocdp.com/cdp-sdk/observer/leo.proxy.js` | `https://cdp.scope.vn/cdp-sdk/observer/leo.proxy.js` |
| **(1b) Iframe Proxy** | `https://beta.leocdp.com/cdp-sdk/html/cdp-event-proxy.html` | `https://cdp.scope.vn/cdp-sdk/html/cdp-event-proxy.html` |
| **(1c) Script con trong Iframe** | `https://beta.leocdp.com/cdp-sdk/observer/leo.observer.js` | `https://cdp.scope.vn/cdp-sdk/observer/leo.observer.js` |
| **(2) Endpoint thu nhận Event** | `POST https://beta.leocdp.com/data/api/v1/tracking/logs` | `POST https://cdp.scope.vn/data/api/v1/tracking/logs` |
| **(3) Bucket & Đường dẫn S3/MinIO** | `s3://data-tracking-<data_source_id>/events/YYYY-MM-DD-HH/<batch_uuid>.jsonl.gz` | Giữ nguyên ở tầng Object Storage (VNG vStorage / MinIO) |
| **(4) Hỗ trợ CNAME riêng (`cdp.scope.vn`)** | **Đồng ý (Hỗ trợ sẵn trong code)** | `leo.proxy.js` tự suy luận host từ `script.src` hoặc `window.leoObserverLogDomain` |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Cấu hình route tĩnh & CORS trên Tracking API:**
  - Xem [`customer360-event-api/app.py`](../../customer360-event-api/app.py) (mount cả `/cdp-sdk`, `/data/cdp-sdk`, `/static` và `/api/v1`, `/data/api/v1`; CORS đã mở sẵn cho cross-origin).
- **Cơ chế tự nhận diện CNAME domain trong SDK:**
  - [`leo.proxy.js`](../../customer360-event-api/static/c360-web-sdk/observer/leo.proxy.js) (dòng 88–158) tự động lấy origin từ `window.leoTrackingEndpoint`, `window.leoObserverLogDomain` hoặc chính thẻ `<script src="https://cdp.scope.vn/...">`.
- **Cấu trúc S3 Bucket & Object Key:**
  - Định nghĩa tại [`storage.py`](../../customer360-event-api/core/storage.py) (`build_tracking_object`).
- **Tài liệu:** [`1-web-sdk-tracking.md`](../data-sources/1-web-sdk-tracking.md), [`customer360-event-api/README.md`](../../customer360-event-api/README.md), [`PERF_REPORT.md`](../../customer360-event-api/tests/reports/PERF_REPORT.md), [`production-deployment.md`](../operations/production-deployment.md).

---

## Q85

**Trạng thái:** Còn mở · **Hạn:** 30/10/2026 · **Đặc tả:**  S0 §7.4; I29; Q62, Q67  
**Ảnh hưởng tới:** Quyết định cách bật kích hoạt ra kênh ngoài (B4)  
**Cập nhật:** 21/10/2026  

### Câu hỏi

LeoCDP có lọc được theo trạng thái đồng ý ads_offsite (tách khỏi ads) trước khi đẩy tệp lên Google, Meta, TikTok không? Nếu không, SCOPE phải chặn ở cdp-bridge (không gửi cdp_consent cho người thiếu ads_offsite) hoặc dùng văn bản r2.

### Câu trả lời

> **Đề xuất của SCOPE (21/10/2026):**
> - LeoCDP lọc theo `ads_offsite` (tách khỏi `ads`) trước khi đẩy tệp lên Google, Meta, TikTok.
> - Nếu chưa làm được trước B4: SCOPE chặn ở `cdp-bridge` (không gửi email/SĐT băm của người thiếu `ads_offsite` sang LeoCDP cho mục đẩy tệp) hoặc chạy văn bản `r2` (tắt `ads_offsite`).

#### 1. Tóm tắt trạng thái đáp ứng

| Hạng mục | Trạng thái hiện tại | Phương án xử lý cho B4 |
|---|---|---|
| **Lọc theo `crm_suppression_list` (Global / Campaign)** | **Đã có sẵn** | Tự động loại mọi email/SĐT/profile nằm trong danh sách chặn tại `audience_sync.py`. |
| **Lọc riêng theo cờ `ads_offsite = true`** | **Chưa bật mặc định trong SQL** | **Cách 1 (trên LeoCDP):** Thêm 1 dòng điều kiện `WHERE COALESCE((p.communication_preferences->>'ads_offsite')::boolean, false) = true` vào `audience_sync.py`.<br>**Cách 2 (Dự phòng):** Chặn tại `cdp-bridge` của SCOPE trước B4. |

#### 2. Chi tiết kỹ thuật & Mã nguồn liên quan

- **Truy vấn chọn thành viên đẩy tệp hiện tại:**
  - Tại [`audience_sync.py`](../../customer360-backend/data_synch/audience_sync.py) (dòng 623–644), câu lệnh `SELECT` lấy các hồ sơ từ `cdp_master_profiles` thoả mãn:
    1. `p.status_code = 1` và `%(segment_tag)s = ANY(p.segmentation_tags)`
    2. `p.email IS NOT NULL OR p.phone_number IS NOT NULL`
    3. `NOT EXISTS (SELECT 1 FROM crm_suppression_list sl ...)`
  - Tài liệu [`customer360-backend/data_synch/README.md`](../../customer360-backend/data_synch/README.md) cũng ghi chú rõ: *"Provider-specific consent keys should be added as an explicit connector policy rather than inferred here"*.
- **Vị trí bổ sung điều kiện `ads_offsite`:**
  - Lưu `ads_offsite` trong `cdp_master_profiles.communication_preferences` và thêm điều kiện lọc trực tiếp vào truy vấn trong [`audience_sync.py`](../../customer360-backend/data_synch/audience_sync.py).
- **Tài liệu:** [`customer360-backend/data_synch/README.md`](../../customer360-backend/data_synch/README.md), [`audience_sync.py`](../../customer360-backend/data_synch/audience_sync.py), [`crm.py`](../../customer360-dao/src/leo_customer360_dao/models/crm.py).
