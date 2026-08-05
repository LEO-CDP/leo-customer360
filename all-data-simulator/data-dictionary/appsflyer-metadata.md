# Metadata Raw Log AppsFlyer – Customer 360

## 1. Giới thiệu

Tài liệu này mô tả metadata (từ điển dữ liệu) của raw log AppsFlyer được sử
dụng trong nền tảng Customer 360 (CDP), dựa trên hai nguồn:

- [appsflyer-dictionary.csv](appsflyer-dictionary.csv) — từ điển đầy đủ các
  trường dữ liệu AppsFlyer công bố chính thức (Push API / Data Locker /
  Pull API), dùng làm chuẩn tham chiếu tên trường – kiểu dữ liệu – ý nghĩa
  nghiệp vụ.
- [appsflyer_faker.py](../appsflyer_faker.py) — script mô phỏng dữ liệu cho một
  ứng dụng doanh nghiệp mẫu, sinh ra file `bank123_appsflyer_in_app_events.csv`
  có cấu trúc giống một báo cáo
  **Pull API raw-data report** thật của AppsFlyer (cột `event_time`/
  `event_name`/`event_value` cho các sự kiện in-app: `install`, `Login`, các
  feature trong app, `af_app_reopen` cho retargeting).

Raw log AppsFlyer là một trong các nguồn dữ liệu đầu vào (cùng với MoEngage,
Web Tracking) được nạp vào `cdp_raw_profiles_stage` và sau đó được **Customer
Identity Resolution (CIR)** trong `backend-system/identity_resolution` xử lý
để hợp nhất thành `cdp_master_profiles`.

## 2. Phân nhóm trường dữ liệu (Field groups)

| Nhóm | Ý nghĩa |
|---|---|
| Attribution | Thông tin quy gán chiến dịch/quảng cáo: media source, campaign, adset, ad, cost, click/impression, referrer... |
| Device info | Thông tin thiết bị và định danh thiết bị/quảng cáo: `idfa`, `idfv`, `advertising_id`, `android_id`, `imei`, `appsflyer_id`, `customer_user_id`... |
| Device location | Vị trí địa lý suy ra từ IP thiết bị: `ip`, `country_code`, `city`, `state`, `postal_code`, `dma` |
| App | Thông tin ứng dụng: `app_id`, `app_name`, `bundle_id`, `app_version`, `sdk_version` |
| Event | Sự kiện in-app: `event_name`, `event_time`, `event_value`, doanh thu sự kiện |
| IAP / Subscription | Chi tiết giao dịch mua trong ứng dụng và thuê bao (Purchase SDK connector, True Revenue API) |
| Ad revenue | Doanh thu quảng cáo (Ad revenue API): `impressions`, `placement`, `mediation_network`... |
| Protect360 | Chống gian lận: `blocked_reason`, `fraud_reason`, `is_organic`... |

## 3. Danh mục đầy đủ trường dữ liệu

> Nguồn: [appsflyer-dictionary.csv](appsflyer-dictionary.csv). Cột **Pull
> API** cho biết trường có xuất hiện trong report Pull API (định dạng mà
> `appsflyer_faker.py` mô phỏng) hay không: **Có** / **Không** / **Một phần**
> (nằm lồng trong object `event_value` dạng JSON của sự kiện in-app).

### 3.1 Attribution

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `ad_placement` | Vị trí quảng cáo trên CTV | String 64 | Banner 1 | Có |
| `af_ad` | Tên quảng cáo | String 100 | Summer Promo | Có |
| `af_ad_id` | ID quảng cáo | String 24 | 1234567890 | Có |
| `af_ad_type` | Loại quảng cáo (banner, footer...) | String 24 | banner | Có |
| `af_adset` | Tên adset | String 100 | Prospecting iOS | Có |
| `af_adset_id` | ID adset | String 24 | 9876543210 | Có |
| `af_attribution_lookback` | Khoảng thời gian tối đa để install được gán cho quảng cáo | 3 char max | 7d | Có |
| `af_c_id` | ID chiến dịch | String | 123456789 | Có |
| `af_channel` | Kênh nguồn truyền thông (YouTube, Instagram...) | Dynamic Enum 20 | YouTube | Có |
| `af_cost_currency` | Mã tiền tệ chi phí (ISO-4217), mặc định USD | String 3 | USD | Có |
| `af_cost_model` | Mô hình chi phí (CPC/CPI/CPM...) | String 20 | CPI | Có |
| `af_cost_value` | Giá trị chi phí | String 20 | 3.2500 | Có |
| `af_engagement_destination` | Loại đích quảng cáo dẫn tới (app, web, CTV...) | String | app | Có |
| `af_keywords` | Từ khóa tìm kiếm theo báo cáo ad network | String 100 | running shoes | Có |
| `af_prt` | Mã agency (partner) | String | agency_name | Có |
| `af_reengagement_window` | Cửa sổ thời gian gán sự kiện cho retargeting | String 3 | 30d | Có |
| `af_siteid` | ID publisher | String 24 | publisher_123 | Có |
| `af_sub[n]` (n=1-5) | Tham số tùy chỉnh của advertiser trong attribution link | String 100 | sample | Có |
| `af_sub_siteid` | ID sub-publisher | String 50 | sub_pub_456 | Có |
| `attributed_touch_time` | Thời điểm lượt chạm được gán cho conversion | Date Time | 2026-08-05 14:30:00 | Có |
| `attributed_touch_type` | Loại lượt chạm: click / impression / pre-installed | Enum 10 | click | Có |
| `campaign` | Tên chiến dịch | String 100 | Summer Sale 2026 | Có |
| `campaign_type` | Nguồn mang người dùng: UA / Organic / Retargeting / Unknown | String | UA | Có |
| `contributor[n]_af_prt` (n=1-3) | Agency/PMD của contributor | String 50 | agency_name | Có |
| `contributor[n]_campaign` | Chiến dịch của contributor | String 100 | Summer Sale 2026 | Có |
| `contributor[n]_match_type` | Match type của contributor | String 50 | id_matching | Có |
| `contributor[n]_media_source` | Media source của contributor | String 50 | google_ads | Có |
| `contributor[n]_touch_time` | Thời điểm chạm của contributor | Date Time | 2026-08-05 14:30:00 | Có |
| `contributor[n]_touch_type` | Loại chạm của contributor | Enum 10 | click | Có |
| `contributor_[n]_engagement_type` | Loại tương tác của contributor | Enum | click | Có |
| `conversion_type` | Loại chuyển đổi: install / reinstall / re-engagement / unknown | String | Install | Có |
| `custom_data` | Dữ liệu tùy chỉnh do advertiser gửi qua SDK/S2S | String \| Object | `{"plan":"premium"}` | Có |
| `engagement_type` | Loại tương tác: click_to_download, engaged_click... | String | click_to_app | Có |
| `event_source` | Nguồn sự kiện: SDK hoặc S2S | Enum 3 | SDK | Có |
| `gp_broadcast_referrer` | Google Play Broadcast Referrer | String 1024 | utm_source=google | Có |
| `gp_click_time` | Thời điểm mở trang Google Play sau khi click quảng cáo | Timestamp | 2026-08-05 14:00:00 | Có |
| `gp_install_begin` | Thời điểm bắt đầu cài đặt (Google API) | Timestamp | 2026-08-05 13:55:00 | Có |
| `gp_referrer` | URL referrer của gói đã cài | String 1024 | utm_source=google | Có |
| `http_referrer` | Trang web dẫn tới AppsFlyer click URL | String 10.000 | https://example.com/referrer | Có |
| `install_app_store` | Cửa hàng Android nơi app được tải | String 50 | Google Play | Có |
| `install_time` | Lần mở đầu tiên sau khi cài đặt/re-attribution | Date Time | 2026-08-05 14:30:00 | Có |
| `is_primary_attribution` | UA: True; Retargeting: theo cửa sổ re-engagement | Enum \| Boolean | true | Có |
| `is_retargeting` | UA: False; Retargeting: True | Enum \| Boolean | false | Có |
| `keyword_id` | ID từ khóa do ad network trả về | String 100 | kw_12345 | Có |
| `keyword_match_type` | Kiểu khớp từ khóa (Google AdWords) | String 100 | broad | Có |
| `match_type` | Phương thức attribution: SRN, id_matching, probabilistic, deeplink... | String 50 | id_matching | Có |
| `media_source` | Nguồn truyền thông được gán cho sự kiện | String 150 | google_ads | Có |
| `network_account_id` | ID tài khoản advertiser trên ad network | String \| Integer | acct_12345 | Có |
| `original_url` | URL click/impression đã dùng | String 10.000 | https://example.com/landing?... | Có |
| `retargeting_conversion_type` | UA: Re-install; Retargeting: Re-engagement/Re-attribution (sắp ngừng dùng) | Enum 14 | Re-engagement | Có |
| `store_product_page` | Trang sản phẩm tùy chỉnh trong App Store (iOS 15+) | String 100 | custom_page_1 | Có |

### 3.2 Device info

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `ad_personalization_enabled` | Người dùng có đồng ý Google dùng dữ liệu cho quảng cáo cá nhân hóa | Boolean | true | Có |
| `ad_user_data_enabled` | Người dùng có đồng ý Google dùng dữ liệu cho đo lường/quảng cáo cá nhân hóa | Boolean | true | Có |
| `advertising_id` | Mã quảng cáo có thể đặt lại (GAID Android, CTV ID) | String 40 | 123456789 | Có |
| `amazon_aid` | Mã quảng cáo có thể đặt lại trên thiết bị Amazon | String 100 | amz-1234567890 | Export only |
| `android_id` | Mã thiết bị Android cố định | String 20 | a1b2c3d4e5f6a7b8 | Có |
| `appsflyer_id` | ID duy nhất do SDK AppsFlyer tạo khi cài app; đổi khi gỡ/cài lại | iOS: 24, Android: 33 | 12345678901234567890123456789012 | Có |
| `att` | Trạng thái ATT trên iOS 14+ (not_determined/denied/authorized...) | String 20 | authorized | Có |
| `carrier` | Tên nhà mạng do Android cung cấp | String 50 | Viettel | Có |
| `customer_user_id` | ID người dùng ứng dụng do chủ app đặt (app's own user/login ID) | String \| Integer | user_12345 | Có |
| `deeplink_url` | Đường dẫn deep link nội bộ (chứa `af_dp`) | String 1024 | myapp://product/123 | Có |
| `device_category` | Loại thiết bị: phone/tablet/other | String 20 | phone | Có |
| `device_download_time` | Thời điểm tải xong app (UTC) | Date Time | 2026-08-05 14:30:00 | Có |
| `device_id_type` | Loại platform ID cho CTV (vd RIDA cho Roku) | Enum 4 | RIDA | Không |
| `device_model` | Tên model thiết bị | String 100 | iPhone 15 | Có |
| `gdpr_applies` | Người dùng có thuộc phạm vi GDPR | Boolean | false | Có |
| `idfa` | Mã quảng cáo iOS có thể đặt lại; toàn số 0 nếu không cấp quyền ATT | 40 char max | 00000000-0000-0000-0000-000000000000 | Có |
| `idfv` | Vendor ID do iOS cung cấp | 40 char max | A1B2C3D4-E5F6-7890-1234-56789ABCDEFF | Có |
| `imei` | Mã thiết bị cố định (Android, đã bị hạn chế truy cập từ các OS mới) | 14 char max | 490154203237518 | Có |
| `is_lat` | Người dùng đã bật Limit Ad Tracking hay chưa | Enum 5 (boolean) | false | Có |
| `language` | Ngôn ngữ/locale thiết bị | String 20 | vi-VN | Có |
| `oaid` | ID có thể đặt lại trên một số Android (thay thế GAID) | 40 char max | 8f0b6f28-4f21-4f1d-9e9c-1f3e0a2b7c10 | Có |
| `operator` | Nhà mạng lấy từ SIM MCCMNC | String 50 | Viettel | Có |
| `os_version` | Phiên bản hệ điều hành | String 8 | 18.5 | Có |
| `platform` | Nền tảng thiết bị: iOS/Android/Windows Mobile | Enum 12 | iOS | Có |
| `store_reinstall` | Apple xác định người dùng ASA có phải reinstall (qua Apple user ID) | Boolean \| String | false | Không |
| `user_agent` | User agent của URL/thiết bị | String 1024 | Mozilla/5.0 (iPhone...) | Có |
| `wifi` | Thiết bị đang dùng WIFI hay không | Enum 5 (boolean) | true | Có |

### 3.3 Device location

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `city` | Thành phố suy ra từ IP thiết bị | String 100 | Ho Chi Minh City | Có |
| `country_code` | Mã quốc gia ISO 3166 alpha-2 (UK thay vì GB) | Enum 2 | VN | Có |
| `dma` | Khu vực thị trường theo Nielsen | String 10 | 501 | Có |
| `ip` | Địa chỉ IP (v4/v6) dùng để định vị người dùng | String | 203.0.113.10 | Có |
| `postal_code` | Mã bưu chính từ IP thiết bị (null với SKAN từ 30/08/2021) | String 10 | 700000 | Có |
| `region` | Vùng/bang từ IP thiết bị | String 100 | Southern Vietnam | Có |
| `state` | Bang/tỉnh từ IP thiết bị | String 100 | Ho Chi Minh | Có |

### 3.4 App

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `app_id` | Mã ứng dụng duy nhất trong AppsFlyer | iOS: 12, Android: 100 | com.example.app | Có |
| `app_name` | Tên ứng dụng do advertiser đặt | String 100 | Sample App | Có |
| `app_type` | app_clip hoặc full_app | String | full_app | Có |
| `app_version` | Phiên bản ứng dụng | 8 char max | 1.2.3 | Có |
| `bundle_id` | Bundle ID (iOS) / App ID (Android) | String 100 | com.example.app | Có |
| `sdk_version` | Phiên bản SDK AppsFlyer | String 8 | 6.14.1 | Có |

### 3.5 Event

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `event_name` | Loại sự kiện attribution hoặc tên in-app event | String 100 | purchase | Có |
| `event_revenue` | Giá trị doanh thu sự kiện (SDK) | String 20 | 12.34 | Có |
| `event_revenue_currency` | Mã tiền tệ doanh thu sự kiện (SDK) | String 20 | USD | Có |
| `event_revenue_usd` | Giá trị doanh thu sự kiện (đổi theo công cụ báo cáo) | String \| Decimal | 12.34 | Có |
| `event_revenue_xxx` | Giá trị doanh thu theo tiền tệ được chọn/mặc định | String 20 | 12.34 | Có |
| `event_time` (theo giờ / Postback) | Thời điểm sự kiện làm tròn xuống giờ gần nhất | Date Time | 2026-08-05 14:30:00 | Có |
| `event_time` (SDK) | Thời điểm sự kiện xảy ra | Date Time | 2026-08-05 14:30:00 | Có |
| `event_value` | Dữ liệu chi tiết sự kiện do SDK gửi (≤1000 ký tự) | String 1000 | `{"price":12.34,"currency":"USD"}` | Có |
| `is_receipt_validated` | Trạng thái xác thực receipt (SDK) | Enum \| Boolean | true | Có |

### 3.6 IAP / Subscription

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `af_cancelation_date_ms` | Ngày hủy | — | 2026-08-05 14:30:00 | Không |
| `af_cuids` | Mảng CUID trong ngữ cảnh ARS | — | `["cuid_123","cuid_456"]` | Một phần |
| `af_discount_id` | ID ưu đãi ở lần mua đầu | — | OFFER10 | Một phần |
| `af_discount_type` | Loại giảm giá (introductory, intro price, one_time_code...) | — | introductory | Một phần |
| `af_environment` | production hoặc sandbox | — | production | Một phần |
| `af_expires_date_ms` | Ngày hết hạn chu kỳ subscription hiện tại | — | 2026-12-31 | Một phần |
| `af_net_revenue` | Doanh thu ròng | — | 8.75 | Một phần |
| `af_net_revenue_country` | Mã quốc gia ISO 3166 để áp thuế | — | VN | Một phần |
| `af_net_revenue_factors` | Mảng yếu tố tạo ra net revenue | — | `["store_commission"]` | Một phần |
| `af_net_revenue_postal_code` | Mã bưu chính | — | 700000 | Một phần |
| `af_net_revenue_subdivision` | Mã tiểu bang/tỉnh ISO 3166-2 | — | VN-HCM | Một phần |
| `af_net_revenue_tax_exclusive` | Thuế đã tính vào doanh thu tổng chưa | — | false | Một phần |
| `af_net_revenue_tax_name` | Tên loại thuế (VAT, GST...) | — | VAT | Một phần |
| `af_net_revenue_tax_rate` | Tỷ lệ thuế | — | 10 | Một phần |
| `af_order_id` | ID đơn hàng (Android) | — | ORDER-20260805-001 | Một phần |
| `af_original_transaction_id` | ID giao dịch gốc (iOS) | — | 1000001234567890 | Một phần |
| `af_period_type` | trial / intro / normal | — | normal | Một phần |
| `af_product_id` | ID sản phẩm subscription | — | premium_monthly | Một phần |
| `af_purchase_date_ms` | Ngày mua sản phẩm | — | 2026-08-05 | Một phần |
| `af_purchase_state` | Purchased / Canceled / Pending | — | Purchased | Một phần |
| `af_purchase_token` | Token mua hàng (Android) | — | tok_abc123 | Một phần |
| `af_reason` | Lý do hủy/quay lại/hoàn tiền | — | billing_issue | Một phần |
| `af_refunded_transaction_ids` | Mảng ID giao dịch đã hoàn tiền (iOS) | — | `["1000001234567890"]` | Không |
| `af_store` | Cửa hàng nơi mua subscription | — | app_store | Một phần |
| `af_subscription_ownership_type` | FAMILY_SHARED / PURCHASED | — | PURCHASED | Một phần |
| `af_transaction_id` | ID giao dịch (iOS) | — | 1000001234567890 | Một phần |
| `store_commission` | % hoa hồng cửa hàng nhận | — | 30 | Một phần |

### 3.7 Ad revenue

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `ad_unit` | Tên phân đoạn quảng cáo A/B test | String 1024 | Banner 1 | Một phần |
| `impressions` | Số lần người dùng thấy quảng cáo | String 1024 | 42 | Một phần |
| `mediation_network` | Mạng mediation quảng cáo | String 1024 | admob | Một phần |
| `monetization_network` | Mạng quảng cáo kiếm tiền | String 1024 | admob | Một phần |
| `placement` | Vị trí hiển thị quảng cáo trong app | String 1024 | home_screen | Một phần |
| `segment` | Tên phân đoạn quảng cáo | String 1024 | video_rewards | Một phần |

### 3.8 Protect360 (chống gian lận)

| Trường (API name) | Mô tả | Kiểu dữ liệu | Ví dụ | Pull API |
|---|---|---|---|---|
| `blocked_reason` | Lý do install bị chặn (install_hijacking, bots...) | String 100 | install_hijacking | Một phần |
| `blocked_reason_rule` | (Đã ngừng dùng) | String 100 | validation_rule_1 | Một phần |
| `blocked_reason_value` | Thông tin bổ sung về lý do chặn | String 100 | site_id_123 | Một phần |
| `blocked_sub_reason` | Lý do phụ (referer hijack, CTIT_anomalies...) | String 100 | referer_hijack | Một phần |
| `detection_date` | Ngày fraud được phát hiện | String 100 | 2026-08-05 | Một phần |
| `fraud_reason` | Xem `blocked_reason` | String 100 | bots | Một phần |
| `fraud_sub_reason` | Xem `blocked_sub_reason` | String 100 | referer_hijack | Một phần |
| `is_organic` | Sự kiện in-app có liên quan install organic hay không | String 100 | true | Một phần |
| `rejected_reason` | (Sắp ngừng dùng) hiện chứa blocked reason | String 100 | install_hijacking | Một phần |
| `rejected_reason_value` | Contributor hợp lệ cho install/event bị hijack | String 100 | organic | Một phần |

## 4. Trường thực tế trong dữ liệu mô phỏng (`appsflyer_faker.py`)

`appsflyer_faker.py` chỉ sinh ra **76 cột** tương ứng một tập con của từ điển
ở mục 3 — đúng với những gì một báo cáo Pull API thực tế của AppsFlyer trả về
cho luồng attribution + in-app event (không sinh dữ liệu IAP/Subscription chi
tiết, Ad revenue, hay hầu hết cột Protect360 vì bộ dữ liệu demo không mô
phỏng mua hàng trong app hay gian lận). Danh sách cột output
(`bank123_appsflyer_in_app_events.csv`):

```
attributed_touch_type, attributed_touch_time, click_time, install_time,
media_source, campaign, campaign_id, campaign_type, match_type, is_organic,
fb_campaign_id, agency, channel, keywords, adset_id, adset,
fb_adset_id, fb_adset_name, fb_ad_id, fb_ad_name, ad_id, ad_name, ad_type,
site_id, sub_site_id, sub_param_1..5,
cost_model, cost_value, cost_currency, http_referrer, advertising_id,
idfa, idfv, platform, device_type, os_version, app_version, sdk_version,
app_id, app_name, bundle_id, gp_broadcast_referrer, blocked_reason,
blocked_reason_value, operator, carrier, network_type, wifi, language,
country_code, state, city, postal_code, ip, ua, wifi_mac_address,
imei, android_id, customer_user_id, is_retargeting, reattr_touch_time,
reattr_touch_type, is_primary_attribution, att, conversion_type,
event_time, event_name, event_value
```

Lưu ý: `appsflyer_id` (SDK persistent device ID) **không** có trong dữ liệu mô
phỏng hiện tại — `customer_user_id` (được sinh từ họ tên đã chuẩn hoá +
số ngẫu nhiên, xem `DeviceManager._clean_name`) đóng vai trò định danh người
dùng ổn định cho một thiết bị/persona xuyên suốt các sự kiện.

## 5. CID Fields — Trường phục vụ Customer Identity Resolution (CIR)

Đây là các trường trong raw log AppsFlyer **sẵn sàng dùng làm khóa định danh**
khi nạp vào `cdp_raw_profiles_stage` và chạy qua
`backend-system/identity_resolution` (`CustomerIdentityResolver`). Việc phân
loại dựa trên cấu hình định danh thực tế trong
[resolver.py](../../backend-system/identity_resolution/identity_resolution/resolver.py):
`ARRAY_IDENTITY_FIELDS` (`device_id`, `advertising_id`, `cookie_id`),
`JSONB_KEYED_IDENTITY_FIELDS` (`external_customer_id`) và
`SCALAR_MERGE_FIELDS` (`full_name`, `email`, `phone_number`, `national_id`).

### 5.1 Khóa định danh chính (deterministic identity keys)

| Trường AppsFlyer | Ánh xạ vào cột CIR | Vai trò | Ghi chú |
|---|---|---|---|
| `appsflyer_id` | `device_id` (ứng viên ưu tiên) | Định danh thiết bị/lượt cài đặt do AppsFlyer SDK tự sinh, ổn định cho tới khi gỡ/cài lại app | Không có trong dữ liệu mô phỏng hiện tại (mục 4); cần bổ sung nếu muốn dùng làm `device_id` chuẩn |
| `idfv` (iOS) | `device_id` | Vendor ID ổn định trên thiết bị iOS, không bị ảnh hưởng bởi ATT | Dùng khi `appsflyer_id` không sẵn có |
| `android_id` (Android) | `device_id` | ID thiết bị Android cố định | Có thể đổi khi factory reset |
| `idfa` (iOS) | `advertising_id` | Mã quảng cáo iOS, chỉ có giá trị thật khi người dùng cấp quyền ATT (`att = authorized`) | Toàn số 0 nếu ATT chưa cấp quyền/từ chối — **không** dùng làm khóa khớp trong trường hợp này |
| `advertising_id` (GAID, Android) | `advertising_id` | Mã quảng cáo Android có thể đặt lại | Bị vô hiệu (zero) nếu người dùng bật "opt out of ads personalization" |
| `customer_user_id` | `external_customer_id` | ID người dùng do ứng dụng của doanh nghiệp gán khi đăng nhập/đăng ký, gắn qua source_system = `AppsFlyer` | Khóa nối liên-nguồn quan trọng nhất: cùng một `customer_user_id`/`external_customer_id` do app cấp cho phép khớp một khách hàng xuyên suốt AppsFlyer/MoEngage/WebTracking |
| `imei` (Android, legacy) | `device_id` (dự phòng) | Định danh thiết bị cố định | Đã bị hầu hết OS/Google Play Services chặn truy cập; chỉ còn giá trị lịch sử, **không khuyến nghị** làm khóa khớp chính cho dữ liệu mới |

### 5.2 Trường hỗ trợ định danh/làm giàu hồ sơ (không phải khóa khớp trực tiếp)

| Trường AppsFlyer | Vai trò trong CIR |
|---|---|
| `ip`, `user_agent`, `device_model`, `os_version`, `platform`, `carrier` | Dấu vân tay thiết bị (device fingerprint) — hỗ trợ đối soát xác suất (probabilistic matching) hoặc điều tra thủ công, không phải khóa khớp deterministic đang bật trong `cdp_profile_attributes` |
| `country_code`, `city`, `state`, `postal_code` | Làm giàu hồ sơ vị trí, không dùng để khớp danh tính |
| `media_source`, `campaign`, `channel`, `af_prt` | Thuộc tính về nguồn/kênh mua khách hàng, phục vụ phân tích thu hút (acquisition), không phải khóa CIR |
| `event_name`, `event_time`, `event_value` | Nội dung sự kiện dùng để tính engagement/hành vi sau khi đã resolve danh tính, không dùng để khớp |

### 5.3 Lưu ý bảo mật & quyền riêng tư

- `idfa`/`advertising_id`/`att`: chỉ hợp lệ khi người dùng đã đồng ý theo cơ
  chế ATT (iOS) hoặc chưa bật giới hạn quảng cáo (Android). Giá trị toàn số 0
  **không được** dùng để khớp — nếu không sẽ khiến nhiều khách hàng khác nhau
  bị gộp nhầm thành một `master_profile` (giá trị trùng lặp giả).
- `imei` là dữ liệu định danh thiết bị nhạy cảm và ngày càng ít khả dụng theo
  chính sách hệ điều hành; tránh dùng làm khóa khớp chính, chỉ giữ tham khảo.
- `customer_user_id`/`external_customer_id` chỉ đáng tin khi được gán **sau
  khi khách hàng đăng nhập/xác thực**; các sự kiện `install` (trước đăng
  nhập) thường không có PII và chỉ mang `device_id`/`advertising_id` — đúng
  như luồng `_install_event()` → `_touch_event()` trong
  `scripts/init_sample_data.py` của `identity_resolution`.
- Không có trường PII (email/số điện thoại/họ tên) nào nằm trực tiếp trong
  raw log AppsFlyer theo từ điển ở mục 3 — các trường `SCALAR_MERGE_FIELDS`
  (`full_name`, `email`, `phone_number`, `national_id`) chỉ xuất hiện khi được
  ứng dụng của doanh nghiệp gắn thêm vào payload sự kiện
  (`custom_data`/`event_value`) sau khi khách hàng đăng nhập, tuân thủ nguyên
  tắc *never expose PII across tenants* và luôn gắn kèm `tenant_id`.

## 6. Tài liệu tham khảo

- [appsflyer-dictionary.csv](appsflyer-dictionary.csv)
- [appsflyer_faker.py](../appsflyer_faker.py)
- [backend-system/identity_resolution/identity_resolution/resolver.py](../../backend-system/identity_resolution/identity_resolution/resolver.py)
- [backend-system/identity_resolution/scripts/init_sample_data.py](../../backend-system/identity_resolution/scripts/init_sample_data.py)
