---
title: "Tài liệu Metadata đầy đủ: Trình mô phỏng nguồn nền tảng"
subtitle: ""
author: "Trieu Nguyen"
date: "Ngày 26 tháng 8 năm 2026"
# XeLaTeX on the current environment has no Vietnamese Babel module.
lang: en-US
toc: true
toc-depth: 2
colorlinks: true
linkcolor: blue
urlcolor: blue

geometry:
  - a4paper
  - margin=1.5cm

linestretch: 1.02

mainfont: "DejaVu Serif"
documentclass: article
papersize: a4
fontsize: 10pt

header-includes: |
  \usepackage{microtype}
  \usepackage{xurl}
  \usepackage{enumitem}
  \setlength{\tabcolsep}{3pt}
  \renewcommand{\arraystretch}{1.08}
  \setlength{\emergencystretch}{3em}
  \setlength{\LTleft}{0pt}
  \setlength{\LTright}{0pt}
  \setlist[itemize]{leftmargin=*,itemsep=1pt,topsep=2pt}
  \setlist[enumerate]{leftmargin=*,itemsep=1pt,topsep=2pt}
---

## Tóm tắt

Tài liệu này là hợp đồng metadata cho `all-data-simulator/full_raw_data_simulator.py`.
Tài liệu mô tả nền tảng nguồn được đại diện bởi từng dataset, schema CSV được sinh ra,
kiểu dữ liệu và ý nghĩa của từng field, giá trị định danh, cũng như khác biệt giữa
payload thực tế của nền tảng và biểu diễn test dạng phẳng của trình mô phỏng này.

### Sơ đồ luồng dữ liệu

\begin{center}
\small
\setlength{\tabcolsep}{4pt}
\begin{tabular}{c@{\quad}c@{\quad}c@{\quad}c@{\quad}c}
\fcolorbox{black}{gray!10}{\parbox{0.22\linewidth}{\centering
	{Data sources}\\[3pt]
Meta Lead Ads\\
TikTok Ads\\
Google Analytics 4\\
Zalo Official Account\\
Adjust Pull API}}
& $\longrightarrow$ &
\fcolorbox{black}{gray!10}{\parbox{0.18\linewidth}{\centering
	{Fields}\\[3pt]
Identity fields\\
Profile fields\\
Event fields\\
Campaign fields}}
& $\longrightarrow$ &
\fcolorbox{black}{gray!10}{\parbox{0.22\linewidth}{\centering
	{Identity Resolution}\\[3pt]
Normalize\\
Match\\
Merge customer identities}}
\end{tabular}
\end{center}

\newpage 

## 1. Phạm vi tài liệu

### Các dataset nguồn

| Dataset | Nền tảng nguồn | Grain |
|---|---|---|
| `meta_cir_api.csv` | Meta Lead Ads | Một lead trên mỗi dòng |
| `tiktok_cir_api.csv` | TikTok Ads | Một dòng cho mỗi ad và ngày báo cáo |
| `ga4_cir_api.csv` | Google Analytics 4 | Một dòng event/report tổng hợp giả lập |
| `zalo_cir_api.csv` | Zalo Official Account | Một dòng profile người dùng |
| `adjust_cir_api.csv` | Adjust | Một event trong ứng dụng trên mỗi dòng |

### Danh mục generator

| Generator | Dataset |
|---|---|
| `generate_meta_data` | `meta_cir_api.csv` |
| `generate_tiktok_data` | `tiktok_cir_api.csv` |
| `generate_ga4_data` | `ga4_cir_api.csv` |
| `generate_zalo_data` | `zalo_cir_api.csv` |
| `generate_adjust_data` | `adjust_cir_api.csv` |

### Metadata runtime và output

| Hạng mục | Giá trị |
|---|---|
| Số dòng mặc định | `20` dòng cho mỗi dataset |
| Số lượng identity dùng chung | `8` người, được tái sử dụng theo chu kỳ |
| Ngày/giờ cơ sở | `2026-08-01 09:00:00` |
| Random seed | `42` |
| Encoding CSV | `utf-8-sig` (UTF-8 có BOM) |
| Chế độ xuống dòng CSV | `newline=""` |
| Thư mục output | `all-data-simulator/platform_cir_csv/` |
| ZIP archive | `platform_cir_api_csv_simulated.zip` trong thư mục `all-data-simulator/` |
| Nội dung ZIP | Năm file CSV được sinh ra, lưu bằng basename |
| Dictionary key bị thiếu | Bị bỏ qua bởi `csv.DictWriter` vì `extrasaction="ignore"` |

Các output path tương đối được phân giải dựa trên thư mục làm việc của process,
không dựa trên thư mục chứa script. Lệnh được ghi trong tài liệu này giả định
thư mục làm việc là thư mục gốc của repository.

## 2. Metadata identity dùng chung

`PEOPLE` là nguồn identity giả lập được dùng để hỗ trợ identity resolution giữa các
nền tảng. Với row index `i`, mỗi generator chọn
`PEOPLE[i % len(PEOPLE)]`, ngoại trừ TikTok vì đây là dữ liệu quảng cáo tổng hợp và
không sử dụng bản ghi của một cá nhân.

| Attribute | Type | Ví dụ | Mục đích |
|---|---|---|---|
| `cid` | string | `CUST-0001` | Khóa customer giả lập; xuất hiện trong `customer_user_id` của Adjust và JSON `event_value` |
| `name` | string | `Nguyen An` | Tên hiển thị đầy đủ cho Meta và Zalo |
| `first` | string | `An` | Tên gọi trong Meta lead |
| `last` | string | `Nguyen` | Họ trong Meta lead |
| `email` | string | `an.nguyen@example.test` | Email trong Meta lead; là dữ liệu giả lập và không gửi được |
| `phone` | string | `84901234001` | Số điện thoại Meta/Zalo; các chữ số được tạo theo dạng giống dữ liệu đã chuẩn hóa |
| `city` | string | `Hanoi` | Field ngữ cảnh địa lý và identity |
| `state` | string | `Hanoi` | Field ngữ cảnh địa lý và identity |
| `country` | string | `VN` | Mã quốc gia dạng ISO được dùng trong các row của nền tảng |
| `gender` | integer | `1` | Mã gender mô phỏng của Zalo; comment trong code quy định `1` là nam, `2` là nữ, `0` là khác/không xác định |

Việc liên kết identity là dữ liệu giả lập. Các platform ID thực tế không được dùng
làm customer key dùng chung. Các CIR key được mô phỏng mạnh nhất là email, phone,
name và cặp `customer_user_id`/`event_value.customer_id` của Adjust.

\newpage

## 3. Metadata Meta Lead Ads

### Nguồn tài liệu API

- Hướng dẫn chính: [Meta Lead Ads retrieval guide](https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving/)
- Lead node: [Meta Lead object reference](https://developers.facebook.com/docs/graph-api/reference/lead/)
- Lead form: [Meta Leadgen Form reference](https://developers.facebook.com/docs/graph-api/reference/leadgen_form/)

Một Meta lead thực tế là một object. Các câu trả lời của form thường được biểu diễn
trong collection lồng nhau có tên `field_data`. Trình mô phỏng này xuất ra projection
CSV dạng phẳng để phục vụ ingestion và test CIR.

### Metadata dataset

| Hạng mục | Giá trị |
|---|---|
| File | `meta_cir_api.csv` |
| Grain | Một lead giả lập |
| Số dòng | `Config.NUM_ROWS` |
| Source identifier chính | `id` |
| Identity field | `field_email`, `field_phone`, `field_first_name`, `field_last_name`, `field_city`, `field_state`, `field_country` |
| Time field | `created_time` |
| Định dạng thời gian | `YYYY-MM-DDTHH:MM:SS+0000` |
| Flattening | Câu trả lời form trở thành các column `field_*`; hierarchy campaign cũng được làm phẳng |

### Metadata column

| Column | Type | Nullability | Ý nghĩa và cách sinh |
|---|---|---|---|
| `id` | string | non-null | ID object Meta lead giả lập, có định dạng `32800000000NNNN`; là primary key của trình mô phỏng |
| `created_time` | string datetime | non-null | `Config.BASE_DATE + i*4 hours + random minute`, định dạng với offset `+0000` |
| `form_id` | string | non-null | ID lead form giả lập cố định `890000123456789` |
| `campaign_id` | string | non-null | ID campaign giả lập; hậu tố ngẫu nhiên từ 1 đến 4 nên có thể lặp lại |
| `campaign_name` | string | non-null | Nhãn campaign giả lập; được random độc lập từ 1 đến 4 |
| `adset_id` | string | non-null | ID ad set giả lập; hậu tố ngẫu nhiên từ 1 đến 5 |
| `adset_name` | string | non-null | Tên ad set giả lập cố định `Broad_Audience_18_65` |
| `ad_id` | string | non-null | ID ad giả lập; hậu tố ngẫu nhiên từ 1 đến 7 |
| `ad_name` | string | non-null | Tên ad giả lập; hậu tố creative ngẫu nhiên từ 1 đến 7 |
| `field_email` | string email | non-null | Câu trả lời email từ người giả lập dùng chung; Meta CIR key mạnh nhất |
| `field_phone` | string phone | non-null | Câu trả lời phone từ người giả lập dùng chung; là Meta CIR key mạnh nhất sau khi normalize |
| `field_first_name` | string | non-null | Câu trả lời first name trong form |
| `field_last_name` | string | non-null | Câu trả lời last name trong form |
| `field_city` | string | non-null | Câu trả lời city trong form |
| `field_state` | string | non-null | Câu trả lời state/province trong form |
| `field_country` | string ISO-like code | non-null | Câu trả lời country trong form, hiện là `VN` |

### Giới hạn của trình mô phỏng

- Tên `field_*` là tên column của trình mô phỏng, không phải bản sao trực tiếp của
  cấu trúc `field_data` lồng nhau trong response object của Meta.
- ID campaign, ad set và ad không được đảm bảo unique hoặc ổn định theo entity.
- Không mô phỏng quyền Meta API, access token, pagination khi lấy lead hoặc field webhook.

## 4. Metadata TikTok Integrated Reports

### Nguồn tài liệu API

- Tổng quan API portal: [TikTok Marketing API reporting overview](https://business-api.tiktok.com/portal/docs?id=1738864835805186)
- Nhóm endpoint integrated report: `/open_api/v1.3/report/integrated/get/`

TikTok report bao gồm dimension và metric. Trình mô phỏng mô hình hóa một basic report
ở cấp ad, được group theo ngày và country. API thực tế thường trả các giá trị số dưới
dạng string trong JSON; CSV simulator này ghi các giá trị số Python, vì vậy CSV serializer
sẽ ghi chúng không có dấu quote.

### Metadata dataset

| Hạng mục | Giá trị |
|---|---|
| File | `tiktok_cir_api.csv` |
| Grain | Một dòng ad/ngày báo cáo giả lập |
| Số dòng | `Config.NUM_ROWS` |
| Dimension | Advertiser, date, campaign, ad group, ad, country |
| Metric | Impressions, clicks, spend, CTR, CPC, CPM, conversions, conversion rate |
| Ngày báo cáo | `stat_time_day` |
| Định dạng ngày báo cáo | `YYYY-MM-DD 00:00:00` |
| Currency | Không khai báo rõ; `spend` là số tiền giả lập theo account currency |

### Metadata column

| Column | Type | Nullability | Ý nghĩa và cách sinh |
|---|---|---|---|
| `advertiser_id` | string | non-null | ID advertiser giả lập cố định `710000123456789` |
| `stat_time_day` | string datetime | non-null | `Config.BASE_DATE + (i % 10) days`, được chuẩn hóa về nửa đêm |
| `campaign_id` | string | non-null | ID campaign giả lập; hậu tố ngẫu nhiên từ 1 đến 4 |
| `campaign_name` | string | non-null | Tên campaign giả lập; được random độc lập từ 1 đến 4 |
| `adgroup_id` | string | non-null | ID ad group giả lập; hậu tố ngẫu nhiên từ 1 đến 5 |
| `adgroup_name` | string | non-null | Tên ad group giả lập; hậu tố ngẫu nhiên từ 1 đến 5 |
| `ad_id` | string | non-null | ID ad giả lập; hậu tố ngẫu nhiên từ 1 đến 7 |
| `ad_name` | string | non-null | Tên ad giả lập; hậu tố ngẫu nhiên từ 1 đến 7 |
| `country_code` | string ISO-like code | non-null | Chọn ngẫu nhiên từ `VN`, `SG`, `TH`; `VN` có bốn phần tử nên có trọng số cao hơn |
| `impressions` | integer | non-null | Số nguyên ngẫu nhiên từ 10.000 đến 20.000 |
| `clicks` | integer | non-null | Cắt phần thập phân của impressions nhân với tỷ lệ ngẫu nhiên từ 1% đến 4% |
| `spend` | decimal | non-null | Làm tròn đến hai chữ số; clicks nhân với số ngẫu nhiên từ 0.3 đến 0.8 |
| `ctr` | decimal percent | non-null | `clicks / impressions * 100`, làm tròn đến bốn chữ số |
| `cpc` | decimal | non-null | `spend / clicks`, làm tròn đến bốn chữ số; bằng zero nếu clicks bằng zero |
| `cpm` | decimal | non-null | `spend / impressions * 1000`, làm tròn đến bốn chữ số |
| `conversion` | integer | non-null | Cắt phần thập phân của clicks nhân với tỷ lệ ngẫu nhiên từ 1% đến 5% |
| `conversion_rate` | decimal percent | non-null | `conversion / clicks * 100`, làm tròn đến bốn chữ số; bằng zero nếu clicks bằng zero |

### Giới hạn của trình mô phỏng

- Không mô phỏng report request, access token, pagination, account timezone, account
  currency hoặc response envelope của API.
- ID dimension và name được random độc lập, vì vậy cùng một ID có thể nhận các name
  khác nhau giữa các row.
- Metric là ước lượng giả lập và không nên được xem là benchmark của nền tảng.

## 5. Metadata Google Analytics 4

### Nguồn tài liệu API

- Schema Data API chính thức: [Google Analytics Data API dimensions and metrics](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
- Các field cấu hình GA4: [Google Analytics configuration reference](https://developers.google.com/analytics/devguides/collection/ga4/reference/config)
- Gửi identity sau khi đăng nhập: [Send user IDs](https://developers.google.com/analytics/devguides/collection/ga4/user-id)
- Gửi user data đã hash: [Send user-provided data using Measurement Protocol](https://developers.google.com/analytics/devguides/collection/ga4/uid-data)
- Payload Measurement Protocol: [Measurement Protocol reference](https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference)
- Chính sách PII: [Best practices to avoid sending PII](https://support.google.com/analytics/answer/6366371)

Dataset này sử dụng tên dimension và metric của Google Analytics Data API. Đây là
projection CSV phẳng của các report row giả lập, không phải event record của GA4
BigQuery export.

### Tích hợp attribution và identity trong GA4

#### UTM source và traffic từ Facebook

Đối với website, GA4 có thể đọc campaign parameter từ landing URL, gồm
`utm_source`, `utm_medium`, `utm_campaign`, `utm_id`, `utm_content` và `utm_term`.
Các giá trị này được dùng cho những traffic-source dimension của GA4 như
`sessionSource`, `sessionMedium`, `sessionCampaignName` và `sessionCampaignId`.

`utm_source=facebook` chỉ cho biết Facebook là traffic source; nó không định danh
customer. `fbclid` của Meta là click identifier, không phải customer ID, `cid` hay
authenticated user identity. Không được dùng `fbclid` làm CIR customer key. Nếu
application lưu giá trị này để chẩn đoán campaign nội bộ, không đưa PII vào campaign
parameter hoặc page URL và phải tuân thủ quy định privacy, retention của dự án.

Không được nhầm các identifier này với nhau: GA4 `client_id` định danh một browser
hoặc app instance, GA4 `user_id` định danh user đã đăng nhập bằng giá trị non-PII do
website sở hữu, `fbclid` định danh một lượt click quảng cáo Meta, còn `cid` trong
simulator định danh một customer giả lập.

Simulator hiện sinh UTM field giả lập và `fbclid` chỉ cho traffic Facebook dưới dạng
collection metadata. Các giá trị này đại diện cho input attribution từ landing page;
không được lấy từ URL hoặc Facebook account thực tế.

#### Google SSO hoặc internal login

Sau khi Google SSO hoặc internal login thành công, website có thể set `user_id` của
GA4 thành một identifier ổn định, do website sở hữu và mang tính pseudonymous. Google
xác định `user_id` là configuration value dành riêng để kết nối hoạt động giữa các
session, device và platform. Giá trị này không được tự nó là PII. Không gửi nó dưới
dạng custom user property, custom dimension hoặc event-level parameter.

Chỉ gửi `user_id` khi user đang authenticated. Khi logout, set giá trị thành `null`;
không gửi empty string hoặc text `"null"`. Simulator biểu diễn row anonymous bằng
`user_id` rỗng và row authenticated bằng giá trị non-PII giả lập. CSV cũng có các
collection field giả lập `client_id`, `user_pseudo_id` và `login_method`.

#### Email và phone đã hash

Khi server-side integration sử dụng GA4 Measurement Protocol, user-provided data có
thể được gửi trong object cấp cao nhất `user_data` cùng với `user_id`. Trong mô hình
này, email và phone không phải event parameter thông thường. Measurement Protocol yêu
cầu developer normalize dữ liệu, hash bằng SHA-256 và gửi giá trị dạng hex lowercase
trong `sha256_email_address` và `sha256_phone_number`.

Quy tắc normalize được document gồm:

- Email: bỏ whitespace ở đầu/cuối, chuyển lowercase, bỏ space và bỏ dấu chấm trước
  domain với địa chỉ `gmail.com` hoặc `googlemail.com`, sau đó hash.
- Phone: bỏ mọi ký tự không phải chữ số, thêm prefix `+` theo dạng E.164, sau đó hash.

Chỉ gửi dữ liệu này khi đã đáp ứng các điều kiện consent, privacy và data governance
cần thiết cho implementation. Không bao giờ gửi email hoặc phone raw trong GA4 URL,
event parameter thông thường hoặc custom dimension. CSV simulator chỉ chứa giá trị
fixture SHA-256 giả lập trong các column `sha256_*`, không chứa email hoặc phone raw.

Ví dụ shape của Measurement Protocol:

```json
{
  "client_id": "WEB_CLIENT_ID",
  "user_id": "NON_PII_INTERNAL_USER_ID",
  "events": [{"name": "login", "params": {"method": "google_sso"}}],
  "user_data": {
    "sha256_email_address": ["SHA256_EMAIL_HEX"],
    "sha256_phone_number": ["SHA256_PHONE_HEX"]
  }
}
```

Các giá trị trong ví dụ chỉ là placeholder. Request thực tế còn yêu cầu credential và
identifier phù hợp cho Measurement Protocol transport.

### Metadata dataset

| Hạng mục | Giá trị |
|---|---|
| File | `ga4_cir_api.csv` |
| Grain | Một event/report row giả lập |
| Số dòng | `Config.NUM_ROWS` |
| Date field | `date` |
| Định dạng date | `YYYYMMDD` |
| Event set | `page_view`, `view_item`, `begin_checkout`, `purchase` |
| Source set | `google`, `facebook`, `tiktok`, `zalo` |
| Revenue currency | Không khai báo rõ; giá trị là số tiền giả lập dạng VND |
| Mô hình key event | `purchase` tạo `keyEvents = 1`; các event khác tạo `0` |
| Mô hình authentication | Mỗi row thứ ba là anonymous; các row khác dùng context login `google_sso` hoặc `internal` giả lập |
| Mô hình UTM | Giá trị `utm_*` giả lập và đồng bộ với session/campaign field |
| Mô hình user data | Row authenticated có email/phone SHA-256 giả lập; raw PII không bao giờ được sinh |

### Metadata column

| Column | Type | Nullability | Ý nghĩa và cách sinh |
|---|---|---|---|
| `date` | string date | non-null | Ngày random từ ngày cơ sở đến 10 ngày sau đó, định dạng `YYYYMMDD` |
| `eventName` | string enum-like | non-null | Event giả lập random từ bộ bốn event |
| `campaignId` | string | non-null | ID giả lập dạng `GAD-C360-NNN`, hậu tố ngẫu nhiên từ 1 đến 4 |
| `campaignName` | string | non-null | Tên web campaign giả lập, được random độc lập từ 1 đến 4 |
| `sessionSource` | string enum-like | non-null | Chọn ngẫu nhiên từ `google`, `facebook`, `tiktok`, `zalo` |
| `sessionMedium` | string enum-like | non-null | Dự kiến là `cpc` với Google và `paid_social` với các source khác; implementation hiện tại so sánh cả danh sách source thay vì source đã chọn, nên hiện sinh `paid_social` cho mọi row |
| `country` | string ISO-like code | non-null | Country của người giả lập dùng chung, hiện là `VN` |
| `city` | string | non-null | City của người giả lập dùng chung |
| `browser` | string enum-like | non-null | Chọn ngẫu nhiên từ `Chrome`, `Safari`, `Edge` |
| `deviceCategory` | string enum-like | non-null | Chọn ngẫu nhiên từ `mobile`, `desktop`, `tablet` |
| `platform` | string enum-like | non-null | Giá trị cố định `web` |
| `sessionCampaignId` | string | non-null | Cùng giá trị được sinh cho `campaignId` |
| `sessionCampaignName` | string | non-null | Tên session campaign giả lập; được random độc lập từ 1 đến 4 |
| `transactionId` | string | rỗng với non-purchase | `ORD-NNNNN` với row purchase; rỗng trong các row khác |
| `eventCount` | integer | non-null | Số nguyên ngẫu nhiên từ 1 đến 5 |
| `keyEvents` | integer | non-null | `1` với purchase và `0` với các event khác; dùng tên metric GA4 hiện đại |
| `totalRevenue` | decimal | non-null | Từ 450.000 đến 950.000 với purchase; `0` với các event khác |
| `engagementRate` | decimal fraction | non-null | Fraction ngẫu nhiên từ 0.35 đến 0.85; theo quy ước fraction của GA4 |
| `client_id` | string | non-null | Browser/app collection identifier giả lập, unique theo generated row; không phải customer ID |
| `user_pseudo_id` | string | non-null | GA4 collection identifier pseudonymous giả lập, unique theo generated row |
| `user_id` | string | rỗng với anonymous row | Site-owned identifier non-PII giả lập dạng `GA4-CUST-NNNN` với authenticated row |
| `login_method` | string | rỗng với anonymous row | Authentication context giả lập: `google_sso` hoặc `internal` |
| `utm_source` | string | non-null | Attribution source giả lập từ landing page; bằng `sessionSource` |
| `utm_medium` | string | non-null | Attribution medium giả lập từ landing page; bằng `sessionMedium`, `cpc` với Google và `paid_social` với source khác |
| `utm_campaign` | string | non-null | Campaign name giả lập từ landing page; bằng `campaignName` và `sessionCampaignName` |
| `utm_id` | string | non-null | Campaign ID giả lập từ landing page; bằng `campaignId` và `sessionCampaignId` |
| `fbclid` | string | rỗng trừ Facebook row | Click identifier Meta giả lập khi `utm_source` là `facebook`; không bao giờ là customer key |
| `sha256_email_address` | string SHA-256 hex | rỗng với anonymous row | Email giả lập đã normalize và hash thành SHA-256 lowercase hexadecimal cho user data dạng Measurement Protocol |
| `sha256_phone_number` | string SHA-256 hex | rỗng với anonymous row | Phone giả lập bỏ về digits, thêm `+`, sau đó hash thành SHA-256 lowercase hexadecimal |

### Giới hạn của trình mô phỏng

- `sessionSource` không được liên kết với session hoặc user identifier thực tế.
- `sessionCampaignName` và `campaignName` không được đảm bảo tương ứng với ID của chúng.
- Không có `userPseudoId`, session ID, event timestamp, item array, user property,
  ecommerce item hoặc request/response envelope của GA4.
- `client_id`, `user_pseudo_id`, `user_id`, UTM field, `fbclid` và các field `sha256_*`
  dạng phẳng là collection metadata của simulator; không phải tất cả đều query được
  như standard dimension của Google Analytics Data API.
- File này không khai báo currency cho `totalRevenue`.
- Implementation của `sessionMedium` hiện có lỗi semantic được mô tả ở trên; consumer
  không nên suy luận rằng source-medium được sinh đúng cho đến khi lỗi được sửa.

## 6. Metadata Zalo Official Account

### Nguồn tài liệu API

- Documentation chính thức: [Zalo Official Account API](https://developers.zalo.me/docs/api/official-account-api/)
- Get User Detail v3.0: [Zalo Get User Detail](https://developers.zalo.me/docs/official-account/quan-ly/quan-ly-thong-tin-nguoi-dung/lay-thong-tin-user)
- Endpoint được đại diện: `/v3.0/oa/user/detail`

Mô hình Zalo OA hiện tại là operation User Detail v3.0. Response thực tế chứa dữ liệu
shared information dạng lồng nhau. Dataset này làm phẳng các thuộc tính profile và
shared-info đã chọn thành các column CSV.

### Metadata dataset

| Hạng mục | Giá trị |
|---|---|
| File | `zalo_cir_api.csv` |
| Grain | Một profile user Zalo giả lập |
| Số dòng | `Config.NUM_ROWS` |
| Source identifier chính | `user_id` |
| Identity field | `display_name`, `shared_info_name`, `shared_info_phone`, `shared_info_city`, `shared_info_address` |
| Privacy/sensitivity field | `is_sensitive`, `user_is_follower` |
| Flattening | Các column `shared_info_*` đại diện cho giá trị được trích xuất từ object `shared_info` thực tế |

### Metadata column

| Column | Type | Nullability | Ý nghĩa và cách sinh |
|---|---|---|---|
| `user_id` | string integer | non-null | User ID Zalo giả lập, base `567826391599986760` cộng với row index |
| `user_id_by_app` | string integer | non-null | User ID theo app giả lập, base `567826390000000000` cộng với row index |
| `display_name` | string | non-null | Tên đầy đủ của người giả lập dùng chung |
| `user_gender` | integer enum-like | non-null | Mã gender của người dùng; comment trong code quy định `1` nam, `2` nữ, `0` khác/không xác định |
| `is_sensitive` | boolean | non-null | Cố định là `False`; đại diện việc profile data trả về có nhạy cảm hay không |
| `user_is_follower` | boolean | non-null | Cố định là `True`; đại diện trạng thái follower |
| `shared_info_name` | string | non-null | Name được làm phẳng từ object `shared_info` giả định |
| `shared_info_phone` | string phone | non-null | Phone được làm phẳng từ object `shared_info`; là identity field cho CIR |
| `shared_info_city` | string | non-null | City được làm phẳng từ object `shared_info` |
| `shared_info_address` | string | non-null | Địa chỉ giả lập dạng `Sample street N, city` |
| `tags_and_notes_info` | string enum-like | non-null | Label giả lập từ bốn tag tiếng Việt; không phải profile identity field được document |

### Giới hạn của trình mô phỏng

- CSV không phải raw JSON response và không giữ lại object `shared_info`.
- `tags_and_notes_info` là enrichment riêng của trình mô phỏng, không thuộc projection
  identity user được mô tả trong tài liệu này.
- Không mô phỏng permission, access token, follower authorization, hành vi privacy hoặc
  response error envelope của Zalo.
- Documentation Zalo được render qua portal dùng nhiều JavaScript; cần đối chiếu field
  response với example v3.0 trực tiếp trước khi dùng CSV này làm integration fixture.

## 7. Metadata Adjust Pull API Raw Data

### Nguồn tài liệu API

- Pull API raw data: [Adjust Pull API raw data](https://support.adjust.com/hc/en-us/articles/360007530258-Pull-API-raw-data)
- Raw data field dictionary: [Adjust raw data field dictionary](https://support.adjust.com/hc/en-us/articles/208387843-Raw-data-field-dictionary)

Adjust raw-data report sử dụng schema rộng và phụ thuộc vào từng report. Field có thể
rỗng hoặc không khả dụng tùy report type, platform, attribution source, privacy setting
và delivery method. Trình mô phỏng này mô hình hóa in-app-event report với một tập field
Pull API canonical đã chọn.

### Metadata dataset

| Hạng mục | Giá trị |
|---|---|
| File | `adjust_cir_api.csv` |
| Grain | Một Adjust in-app event giả lập |
| Số dòng | `Config.NUM_ROWS` |
| Identity field | `customer_user_id`, JSON `event_value.customer_id`, device identifier |
| Event set | `adj_login`, `adj_content_view`, `adj_add_to_cart`, `adj_purchase` |
| Định dạng thời gian | `YYYY-MM-DD HH:MM:SS`, không có timezone rõ ràng |
| Định dạng event value | JSON string; row purchase có `customer_id` và `order_id` |
| Revenue currency | Cố định là `VND` với row có revenue giả lập |

### Metadata column

| Column | Type | Nullability | Ý nghĩa và cách sinh |
|---|---|---|---|
| `attributed_touch_type` | string enum-like | non-null | Chọn ngẫu nhiên `click` hoặc `impression`; loại engagement dùng cho attribution |
| `attributed_touch_time` | string datetime | non-null | Install time trừ ngẫu nhiên từ 5 đến 60 phút |
| `install_time` | string datetime | non-null | Ngày cơ sở cộng ngẫu nhiên 0 đến 5 ngày và 1 đến 12 giờ |
| `event_time` | string datetime | non-null | Install time cộng ngẫu nhiên từ 5 đến 120 phút |
| `event_name` | string enum-like | non-null | Adjust event name random từ bộ bốn event |
| `event_value` | string JSON | non-null | JSON chứa `customer_id`; row purchase có thêm `order_id` |
| `event_revenue` | decimal | non-null | Từ 100.000 đến 500.000 với purchase; `0` trong các row khác |
| `event_revenue_currency` | string currency code | non-null | Cố định là `VND` |
| `event_source` | string enum-like | non-null | Cố định là `SDK` |
| `media_source` | string enum-like | non-null | Chọn random `facebook`, `tiktok`, `googleadwords_int` hoặc `zalo` |
| `channel` | string | non-null | Label hiển thị random: `Facebook Ads`, `TikTok Ads`, `Google Ads` hoặc `Zalo` |
| `campaign` | string | non-null | Tên campaign giả lập; hậu tố random từ 1 đến 4 |
| `campaign_id` | string | non-null | ID Adjust giả lập dạng `ADJ-CAMP-NNN` |
| `adset` | string | non-null | Tên ad set giả lập; hậu tố random từ 1 đến 5 |
| `adset_id` | string | non-null | ID ad set giả lập dạng `ADJ-AS-NNN` |
| `ad` | string | non-null | Tên creative giả lập; hậu tố random từ 1 đến 7 |
| `ad_id` | string | non-null | ID ad giả lập dạng `ADJ-AD-NNN` |
| `country_code` | string ISO-like code | non-null | Country của người giả lập dùng chung, hiện là `VN` |
| `state` | string | non-null | State/province của người giả lập dùng chung |
| `city` | string | non-null | City của người giả lập dùng chung |
| `postal_code` | string | non-null | Postal code giả lập dạng sáu chữ số, từ 700010 đến 700099 |
| `ip` | string IPv4-like | non-null | Địa chỉ dạng IPv4 giả lập bắt đầu bằng `103.21.` |
| `operator` | string | non-null | Chọn random `Viettel`, `MobiFone` hoặc `VinaPhone`; dùng đúng tên field trong Adjust dictionary hiện tại |
| `language` | string BCP-47-like | non-null | Cố định là `vi` |
| `adjust_id` | string | non-null | ID trông giống ID do SDK sinh; segment hai chữ số random nên không đảm bảo unique |
| `advertising_id` | string UUID-like | non-null | Advertising ID có thể reset giả lập; hậu tố theo row index giúp unique trong simulator |
| `idfa` | string | rỗng trên Android | Advertising identifier iOS giả lập; rỗng khi `is_android` là `True` |
| `android_id` | string | rỗng trên iOS | Android identifier giả lập; rỗng khi `is_android` là `False` |
| `customer_user_id` | string | non-null | `cid` giả lập dùng chung; Adjust-to-CIR key mạnh nhất |
| `idfv` | string | rỗng trên Android | iOS vendor identifier giả lập; rỗng khi `is_android` là `True` |
| `platform` | string enum-like | non-null | `android` hoặc `ios`, được chọn bởi `is_android` |
| `device_type` | string | non-null | Nhãn device model giả lập; Adjust dictionary cho biết field này đã deprecated/không còn được populate và nên dùng device model trong report hiện tại |
| `os_version` | string | non-null | Android random `14`/`15` hoặc iOS random `17.6`/`16.7` |
| `app_version` | string | non-null | Cố định là `6.8.1` |
| `sdk_version` | string | non-null | Cố định là `6.15.0` |
| `app_id` | string | non-null | App ID giả lập cố định `com.example.customer360` |
| `bundle_id` | string | non-null | Bundle/app ID giả lập cố định `com.example.customer360` |
| `user_agent` | string | non-null | User agent dạng browser giả lập cho Android hoặc iOS |
| `http_referrer` | string URL | non-null | Landing URL giả lập cố định |
| `original_url` | string URL | non-null | App URL giả lập chứa `cid` của người dùng |

### Giới hạn của trình mô phỏng

- Đây là một subset được chọn từ Adjust main schema, không phải bản export đầy đủ
  của raw-data field dictionary.
- Timestamp string không có timezone, trong khi timezone thực tế của Adjust phụ thuộc
  delivery method và account setting.
- Các giá trị attribution hierarchy được random độc lập và không mô hình hóa entity
  campaign ổn định.
- `adjust_id` có thể collision vì chỉ dùng một suffix random giới hạn; không dùng nó
  làm unique key được đảm bảo trong test.
- Device identifier là dữ liệu giả lập và không được hiểu là device identity thực tế.

## 8. Metadata CIR liên nền tảng

### Khả dụng của identity key

| Nền tảng | Key dùng chung mạnh nhất | Key phụ trợ | Có column `cid` trực tiếp? |
|---|---|---|---|
| Meta | `field_email`, `field_phone` | First/last name, city, state | Không |
| TikTok | Không có | Advertiser/campaign hierarchy và country | Không |
| GA4 | Không có trong schema hiện tại; GA4 production có thể dùng `user_id` không chứa PII | City, country, campaign/source context, traffic dimension tùy chọn sinh từ UTM | Không |
| Zalo | `shared_info_phone` | Name, city, address | Không |
| Adjust | `customer_user_id`, `event_value.customer_id` | Original URL, device identifier, geography | Có, nhưng được nhúng trong field chứ không phải column riêng |

### Mapping row-người dùng giả lập dự kiến

- Meta, GA4, Zalo và Adjust chọn người dùng dùng chung theo chu kỳ.
- Với `20` row và `8` người, vị trí người dùng sẽ lặp lại sau mỗi tám row.
- TikTok row là quan sát quảng cáo tổng hợp và cố ý không có identity cấp cá nhân.
- CIR thực tế nên normalize email và phone trước khi matching; name, city và device
  identifier nên được xem là bằng chứng hỗ trợ, không phải identifier unique được đảm bảo.

### Chất lượng dữ liệu và khả năng tái lập

- Module seed global random generator của Python bằng `42` tại thời điểm import.
- Import module có side effect: tạo output directory đã cấu hình và thay đổi random
  sequence dùng chung của process.
- Khả năng tái lập giả định cùng Python implementation, execution order, configuration
  và generator code.
- CSV được serialize từ các giá trị Python; ingestion downstream nên cast rõ ràng các
  field numeric, boolean, date, datetime và JSON-string thay vì dựa vào type inference.



## 9. Thuật toán CIR liên nền tảng

Phần này mô tả thuật toán tham chiếu trong
`all-data-simulator/test_cir.py`. Đây là implementation deterministic để kiểm tra
identity stitching trên dữ liệu simulator, không phải production identity engine.

### 9.1. Input và nguyên tắc

Thuật toán đọc các file CSV sau từ `./all-data-simulator/platform_cir_csv`:

| Nguồn | Grain | Vai trò trong CIR |
|---|---|---|
| `meta_cir_api.csv` | Một lead | Cung cấp email và phone để nối identity |
| `zalo_cir_api.csv` | Một profile user | Cung cấp phone để nối identity |
| `adjust_cir_api.csv` | Một in-app event | Cung cấp `customer_user_id`, device ID và transaction ID |
| `ga4_cir_api.csv` | Một event/report row | Nối bằng `transactionId` khi có giá trị |
| `tiktok_cir_api.csv` | Một ad/reporting-day row | Dữ liệu aggregate; không có identity cấp customer |

File không tồn tại được loader bỏ qua bằng cách trả về danh sách rỗng. CSV được đọc
bằng `utf-8-sig` để tương thích với UTF-8 BOM do simulator ghi ra.

### 9.2. Identity graph

Graph sử dụng cấu trúc `disjoint-set` (union-find):

- `parent`: map mỗi identifier về parent identifier của nó.
- `records`: map identifier tới các raw record được gắn vào identifier đó.
- `find(node)`: tìm root identity và thực hiện path compression.
- `union(node1, node2)`: đưa hai identifier vào cùng một connected component.
- `add_record(node, platform, record)`: gắn record vào identifier tương ứng.

Identifier được namespace theo prefix để tránh collision giữa các loại ID:

| Prefix | Ví dụ | Ý nghĩa |
|---|---|---|
| `CUID:` | `CUID:CUST-0001` | Customer ID trong CDP Master Profile bridge hoặc Adjust |
| `EMAIL:` | `EMAIL:an.nguyen@example.test` | Email identity |
| `PHONE:` | `PHONE:84901234001` | Phone identity |
| `META:` | `META:328000000000001` | Meta lead ID |
| `ZALO:` | `ZALO:567826391599986760` | Zalo user ID |
| `ADJ:` | `ADJ:174000000010-a1b2c3d4e5f6` | Adjust ID |
| `TXN:` | `TXN:ORD-00003` | Transaction/order ID |

### 9.3. CDP Master Profile bridge giả lập

Trong production, bridge này nên đến từ CDP Master Profile hoặc Customer 360 database. Trong
simulator, `test_cir.py` khai báo tám user cố định với `cuid`, email và phone.
Với mỗi user, thuật toán thực hiện:

```text
CUID:cuid <-> EMAIL:email
CUID:cuid <-> PHONE:phone
```

Bridge này tạo điểm nối trung tâm giữa dữ liệu có PII-like field từ Meta/Zalo và
`customer_user_id` từ Adjust. Dữ liệu thực tế phải được normalize trước khi tạo
key và phải tuân thủ tenant boundary, consent và privacy policy.

### 9.4. Quy tắc nối theo từng nguồn

#### Meta Lead Ads

Với mỗi row có đủ `field_email` và `field_phone`:

1. Tạo `EMAIL:<field_email>` và `PHONE:<field_phone>`.
2. Tạo `META:<id>` từ Meta lead ID.
3. Union email với phone.
4. Union phone với Meta lead ID.
5. Gắn raw row vào node `META:<id>`.

Hiện tại code bỏ qua Meta row nếu thiếu email hoặc phone. Name, city và campaign
field không được dùng để matching.

#### Zalo Official Account

Với mỗi row có `shared_info_phone`:

1. Tạo `PHONE:<shared_info_phone>`.
2. Tạo `ZALO:<user_id>`.
3. Union phone với Zalo user ID.
4. Gắn raw row vào node Zalo.

Zalo hiện chỉ dùng phone làm join key; `display_name`, address và city chỉ nằm trong
timeline record.

#### Adjust

Với mỗi row:

1. Tạo `CUID:<customer_user_id>` và `ADJ:<adjust_id>`.
2. Union customer ID với Adjust ID.
3. Parse `event_value` từ JSON string.
4. Nếu JSON có `order_id`, tạo `TXN:<order_id>` và union transaction với CUID.
5. Gắn raw row vào node Adjust.

Nếu `event_value` không phải JSON hợp lệ, parser bỏ qua transaction link nhưng vẫn
giữ lại Adjust record.

#### GA4

Với mỗi row có `transactionId`:

1. Tạo `TXN:<transactionId>`.
2. Gắn GA4 row vào node transaction.

Đây là join key GA4 duy nhất được `test_cir.py` sử dụng hiện tại. Các field mới trong
simulator như `user_id`, `user_pseudo_id`, `sha256_email_address`,
`sha256_phone_number`, `utm_*` và `fbclid` chưa được dùng trong graph này.

#### TikTok

TikTok không được đưa vào identity graph vì dataset chỉ chứa số liệu quảng cáo
aggregate như `impressions`, `clicks`, `spend` và `conversion`. Không suy luận
customer identity từ `advertiser_id`, campaign ID, ad ID hoặc `country_code`.

### 9.5. Gom nhóm unified profile

Sau khi hoàn tất các phép union:

1. Duyệt toàn bộ node trong `parent` và gọi `find(node)` để lấy root.
2. Gom tất cả identifier vào `profiles[root].identifiers`.
3. Duyệt các record đã gắn trong `records`.
4. Gọi `find(node)` của từng record để đưa record vào `profiles[root].timeline`.
5. Chỉ giữ profile có ít nhất một record trong timeline.

Kết quả mỗi profile có dạng khái niệm:

```json
{
  "identifiers": ["CUID:CUST-0001", "EMAIL:...", "META:..."],
  "timeline": [
    {"platform": "Meta Lead Ads", "data": {"...": "..."}},
    {"platform": "Adjust", "data": {"...": "..."}}
  ]
}
```

`test_cir.py` in summary hai profile đầu tiên, liệt kê identifier và đếm số record
theo platform. Với dữ liệu mặc định hiện tại, lần chạy kiểm tra đã tạo được 13
unified profile có timeline.

### 9.6. Luồng thuật toán

```text
Load CSV sources
      |
      v
Create CDP Master Profile bridge: CUID <-> EMAIL/PHONE
      |
      v
Add Meta, Zalo, Adjust and GA4 identity edges
      |
      v
Find roots with path compression
      |
      v
Group identifiers and records by root
      |
      v
Return unified profiles with cross-platform timeline
```

### 9.7. Các giới hạn cần biết

- Email và phone hiện được so sánh dạng raw; chưa có normalize nhất quán về case,
  whitespace, country code hoặc E.164.
- Hash email/phone của GA4 chỉ có thể match với hash được tạo từ cùng quy tắc
  normalize; không được giải hash để lấy PII.
- `fbclid`, UTM source/medium và campaign field là attribution metadata, không phải
  identity key.
- Không có confidence score, survivorship rule, conflict resolution hoặc manual review.
- Union là quan hệ bắc cầu: một key sai có thể làm nhiều record bị over-merge.
- CDP Master Profile bridge đang hard-code trong test và chưa có tenant isolation, audit trail hoặc
  consent enforcement.
- Adjust `adjust_id` và device identifier là key kỹ thuật; không nên dùng riêng
  chúng làm customer identity chắc chắn.
- GA4 row không có `transactionId` sẽ không được nối vào profile trong implementation
  hiện tại, dù row có thể có `user_id` hoặc hash identity.

### 9.8. Hướng mở rộng production

1. Tạo canonical identity index từ CDP Master Profile với tenant-aware key và audit metadata.
2. Normalize email, phone và các identifier trước khi lookup.
3. Match GA4 `user_id` với một mapping non-PII trong backend; không dùng email/phone
   raw làm `user_id`.
4. Match `sha256_email_address` và `sha256_phone_number` với hash index tương ứng,
   chỉ khi có consent và đúng data-governance policy.
5. Giữ `utm_*` và `fbclid` trong attribution context; không đưa chúng vào identity
   graph.
6. Thêm confidence score, rule ưu tiên key mạnh, conflict queue và human review cho
   các match không chắc chắn.
7. Khi dữ liệu lớn, thay union-find in-memory bằng identity graph/service có persistence,
   partition theo tenant và cơ chế replay/audit.