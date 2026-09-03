# Metadata Raw Log Adjust - Customer 360

## 1. Muc tieu tai lieu

Tai lieu nay mo ta metadata cho nguon du lieu Adjust trong all-data-simulator,
duoc dung de nap vao he thong Customer 360 va phuc vu CIR.

Phien ban nay da duoc cap nhat de dong bo voi:
- all-data-simulator/data-dictionary/adjust-dictionary.csv
- all-data-simulator/adjust_faker.py

Luu y quan trong:
- adjust-dictionary.csv la source of truth cho danh sach field.
- adjust_faker.py la source of truth cho cac cot thuc te duoc xuat ra file CSV demo.

## 2. Nguon tham chieu chinh thuc

Tai lieu nay doi chieu voi cac trang chinh thuc co the truy cap:
- Adjust Report Service API: https://dev.adjust.com/en/api/rs-api/reports/
- Adjust filters_data: https://dev.adjust.com/en/api/rs-api/filters-data/
- Adjust Datascape dimensions glossary: https://help.adjust.com/en/article/datascape-dimensions-glossary
- Adjust Datascape metrics glossary: https://help.adjust.com/en/article/datascape-metrics-glossary

Ghi chu:
- Mot so trang support.adjust.com cu co the bi chan (403) trong moi truong tu dong.
  Vi vay tai lieu uu tien cac nguon dev.adjust.com va help.adjust.com o tren.

## 3. Pham vi du lieu hien tai trong repo

### 3.1 Tong quan schema

- So dong dictionary (khong tinh header): 73
- So cot simulator xuat thuc te: 72
- Do lech co chu y: dictionary co them 1 dong adid de ghi chu thuat ngu chinh thuc,
  nhung cot nay hien chua duoc simulator xuat ra.

### 3.2 Phan nhom field hien co trong dictionary

| Field group | So field | Ghi chu |
|---|---:|---|
| Attribution | 40 | Nguon chien dich, touch, campaign, ad/adset, cost, retargeting |
| Device info | 17 | Device and ad identifiers, network/device traits |
| Device location | 5 | country/state/city/postal_code/ip |
| App | 5 | app_id/app_name/bundle_id/app_version/sdk_version |
| Event | 3 | event_time/event_name/event_value |
| Protect360 | 2 | blocked_reason, blocked_reason_value |
| Privacy | 1 | att |

### 3.3 Danh sach cot simulator xuat thuc te

Danh sach 72 cot output duoc doc truc tiep tu self.headers trong adjust_faker.py:

attributed_touch_type, attributed_touch_time, click_time, install_time,
media_source, campaign, campaign_id, campaign_type, match_type, is_organic,
fb_campaign_id, agency, channel, keywords, adset_id, adset,
fb_adset_id, fb_adset_name, fb_ad_id, fb_ad_name, ad_id, ad_name, ad_type,
site_id, sub_site_id,
sub_param_1, sub_param_2, sub_param_3, sub_param_4, sub_param_5,
cost_model, cost_value, cost_currency, http_referrer, advertising_id,
idfa, idfv, platform, device_type, os_version, app_version, sdk_version,
app_id, app_name, bundle_id, gp_broadcast_referrer, blocked_reason,
blocked_reason_value, operator, carrier, network_type, wifi, language,
country_code, state, city, postal_code, ip, ua, wifi_mac_address,
imei, android_id, customer_user_id, is_retargeting, reattr_touch_time,
reattr_touch_type, is_primary_attribution, att, conversion_type,
event_time, event_name, event_value

## 4. Chinh sua quan trong so voi ban cu

Tai lieu nay da loai bo cac noi dung khong con phu hop voi state hien tai:
- Khong con dung nhan AF cho field class.
- Khong con mo ta AppsFlyer la nha cung cap.
- Khong con coi af_* la naming chinh trong dictionary hien tai.

Luu y ve naming official:
- adid la ten thuat ngu chinh thuc cua Adjust cho device identifier.
- Trong repo hien tai, cot adid chua duoc xuat boi simulator.
- Dictionary giu 1 dong adid de lam reference va de de migrate pipeline ingest
  neu can doi ten truong ve official naming.

## 5. Huong dan CIR cho nguon Adjust

Khi map vao identity_resolution, uu tien cac truong sau:

| Muc dich CIR | Truong de uu tien |
|---|---|
| device_id | adid (neu co), idfv, android_id, imei (legacy fallback) |
| advertising_id | advertising_id, idfa |
| external_customer_id | customer_user_id |
| bo tro giau thong tin profile | country_code, state, city, language, platform, carrier |

Luu y chat luong du lieu:
- idfa va advertising_id co the bi reset hoac zero-value tuy chinh sach quyen rieng tu.
- customer_user_id la khoa lien nguon quan trong nhat neu app cap on dinh sau login.

## 6. Cac file lien quan trong repo

- all-data-simulator/data-dictionary/adjust-dictionary.csv
- all-data-simulator/adjust_faker.py
- backend-system/identity_resolution/identity_resolution/resolver.py
- backend-system/identity_resolution/scripts/init_sample_data.py

## 7. Ket luan

Trang thai hien tai:
- adjust-metadata.md da dong bo voi dictionary va simulator moi.
- Khong con noi dung AppsFlyer trong mo ta metadata Adjust cua simulator.
- Co ghi ro ranh gioi giua official terminology (adid) va output hien tai cua simulator.
