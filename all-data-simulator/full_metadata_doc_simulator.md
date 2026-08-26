---
title: "Full Metadata Document: Platform Source Simulator"
subtitle: ""
author: "Trieu Nguyen"
date: "August 26, 2026"
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

mainfont: "Latin Modern Roman"
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

## Abstract

This document is the metadata contract for `all-data-simulator/full_raw_data_simulator.py`.
It describes the source platform represented by each dataset, the generated CSV schema,
field types and meanings, identity value, and differences between a real platform
payload and this simulator's flattened test representation.

### Overview Flow

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
AppsFlyer Pull API}}
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

## 1. Document Scope

### Source datasets

| Dataset | Source platform | Grain |
|---|---|---|
| `meta_cir_api.csv` | Meta Lead Ads | One lead per row |
| `tiktok_cir_api.csv` | TikTok Ads | One ad/reporting-day row |
| `ga4_cir_api.csv` | Google Analytics 4 | One synthetic event/report row |
| `zalo_cir_api.csv` | Zalo Official Account | One user profile row |
| `appsflyer_cir_api.csv` | AppsFlyer | One in-app event row |

### Generator index

| Generator | Dataset |
|---|---|
| `generate_meta_data` | `meta_cir_api.csv` |
| `generate_tiktok_data` | `tiktok_cir_api.csv` |
| `generate_ga4_data` | `ga4_cir_api.csv` |
| `generate_zalo_data` | `zalo_cir_api.csv` |
| `generate_appsflyer_data` | `appsflyer_cir_api.csv` |

### Runtime and output metadata

| Item | Value |
|---|---|
| Default row count | `20` rows per dataset |
| Shared identity count | `8` people, reused cyclically |
| Base date/time | `2026-08-01 09:00:00` |
| Random seed | `42` |
| CSV encoding | `utf-8-sig` (UTF-8 with BOM) |
| CSV newline mode | `newline=""` |
| Output directory | `all-data-simulator/platform_cir_csv/` |
| ZIP archive | `platform_cir_api_csv_simulated.zip` under `all-data-simulator/` |
| ZIP contents | The five generated CSV files, stored by basename |
| Missing dictionary keys | Ignored by `csv.DictWriter` because `extrasaction="ignore"` |

Relative output paths are resolved from the process working directory, not from the
script directory. The documented command assumes the repository root is the working
directory.

## 2. Shared Identity Metadata

`PEOPLE` is the synthetic identity source used to make cross-platform identity
resolution possible. For row index `i`, each generator selects
`PEOPLE[i % len(PEOPLE)]` except for TikTok, which is aggregate advertising data and
does not use a person record.

| Attribute | Type | Example | Purpose |
|---|---|---|---|
| `cid` | string | `CUST-0001` | Synthetic customer key; appears in AppsFlyer `customer_user_id` and JSON `event_value` |
| `name` | string | `Nguyen An` | Full display name for Meta and Zalo |
| `first` | string | `An` | Meta lead first name |
| `last` | string | `Nguyen` | Meta lead last name |
| `email` | string | `an.nguyen@example.test` | Meta lead email; synthetic and non-deliverable |
| `phone` | string | `84901234001` | Meta/Zalo phone; digits are intentionally normalized-looking |
| `city` | string | `Hanoi` | Geographic identity/context field |
| `state` | string | `Hanoi` | Geographic identity/context field |
| `country` | string | `VN` | ISO-like country code used in platform rows |
| `gender` | integer | `1` | Zalo user gender simulation: code comments define `1` male, `2` female, `0` other/unknown |

Identity linkage is synthetic. Real platform IDs are not used as a shared customer key.
The strongest simulated CIR keys are email, phone, name, and the AppsFlyer
`customer_user_id`/`event_value.customer_id` pair.

\newpage

## 3. Meta Lead Ads Metadata

### Source contract

- Official guide: [Meta Lead Ads retrieval guide](https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving/)
- Lead node: [Meta Lead object reference](https://developers.facebook.com/docs/graph-api/reference/lead/)
- Lead form: [Meta Leadgen Form reference](https://developers.facebook.com/docs/graph-api/reference/leadgen_form/)

A real Meta lead is an object. Form answers are commonly represented in a nested
`field_data` collection. This simulator emits a flat CSV projection for ingestion and
CIR testing.

### Dataset metadata

| Item | Value |
|---|---|
| File | `meta_cir_api.csv` |
| Grain | One synthetic lead |
| Rows | `Config.NUM_ROWS` |
| Primary source identifier | `id` |
| Identity fields | `field_email`, `field_phone`, `field_first_name`, `field_last_name`, `field_city`, `field_state`, `field_country` |
| Time field | `created_time` |
| Time format | `YYYY-MM-DDTHH:MM:SS+0000` |
| Flattening | Form answers become `field_*` columns; campaign hierarchy is also flattened |

### Column metadata

| Column | Type | Nullability | Meaning and generation |
|---|---|---|---|
| `id` | string | non-null | Synthetic Meta lead object ID, formatted as `32800000000NNNN`; simulator primary key |
| `created_time` | string datetime | non-null | `Config.BASE_DATE + i*4 hours + random minute`, formatted with `+0000` offset |
| `form_id` | string | non-null | Fixed synthetic lead form ID `890000123456789` |
| `campaign_id` | string | non-null | Synthetic campaign ID; random suffix from 1 to 4, so values can repeat |
| `campaign_name` | string | non-null | Synthetic campaign label; independently randomized from 1 to 4 |
| `adset_id` | string | non-null | Synthetic ad set ID; random suffix from 1 to 5 |
| `adset_name` | string | non-null | Fixed synthetic ad set name `Broad_Audience_18_65` |
| `ad_id` | string | non-null | Synthetic ad ID; random suffix from 1 to 7 |
| `ad_name` | string | non-null | Synthetic ad name; random creative suffix from 1 to 7 |
| `field_email` | string email | non-null | Email answer from the shared synthetic person; strongest Meta CIR key |
| `field_phone` | string phone | non-null | Phone answer from the shared synthetic person; strongest Meta CIR key after normalization |
| `field_first_name` | string | non-null | First-name form answer |
| `field_last_name` | string | non-null | Last-name form answer |
| `field_city` | string | non-null | City form answer |
| `field_state` | string | non-null | State/province form answer |
| `field_country` | string ISO-like code | non-null | Country form answer, currently `VN` |

### Simulator limitations

- `field_*` names are simulator column names, not a literal copy of a Meta response
  object's nested `field_data` structure.
- Campaign, ad set, and ad IDs are not guaranteed to be unique or stable by entity.
- No Meta API permissions, access tokens, lead retrieval pagination, or webhook fields
  are represented.

\newpage

## 4. TikTok Integrated Reports Metadata

### Source contract

- API portal overview: [TikTok Marketing API reporting overview](https://business-api.tiktok.com/portal/docs?id=1738864835805186)
- Integrated report endpoint family: `/open_api/v1.3/report/integrated/get/`

TikTok reports contain dimensions and metrics. The simulator models an ad-level basic
report grouped by day and country. The real API commonly returns numeric response
values as strings in JSON; this CSV simulator writes Python numeric values, which the
CSV serializer renders without quotes.

### Dataset metadata

| Item | Value |
|---|---|
| File | `tiktok_cir_api.csv` |
| Grain | One synthetic ad/reporting-day row |
| Rows | `Config.NUM_ROWS` |
| Dimensions | Advertiser, date, campaign, ad group, ad, country |
| Metrics | Impressions, clicks, spend, CTR, CPC, CPM, conversions, conversion rate |
| Reporting date | `stat_time_day` |
| Reporting date format | `YYYY-MM-DD 00:00:00` |
| Currency | Not explicitly declared; `spend` is a synthetic account-currency amount |

### Column metadata

| Column | Type | Nullability | Meaning and generation |
|---|---|---|---|
| `advertiser_id` | string | non-null | Fixed synthetic advertiser ID `710000123456789` |
| `stat_time_day` | string datetime | non-null | `Config.BASE_DATE + (i % 10) days`, normalized to midnight |
| `campaign_id` | string | non-null | Synthetic campaign ID; random suffix from 1 to 4 |
| `campaign_name` | string | non-null | Synthetic campaign name; independently randomized from 1 to 4 |
| `adgroup_id` | string | non-null | Synthetic ad group ID; random suffix from 1 to 5 |
| `adgroup_name` | string | non-null | Synthetic ad group name; random suffix from 1 to 5 |
| `ad_id` | string | non-null | Synthetic ad ID; random suffix from 1 to 7 |
| `ad_name` | string | non-null | Synthetic ad name; random suffix from 1 to 7 |
| `country_code` | string ISO-like code | non-null | Random choice from `VN`, `SG`, and `TH`; VN has four entries and is therefore weighted |
| `impressions` | integer | non-null | Random integer from 10,000 to 20,000 |
| `clicks` | integer | non-null | Integer truncation of impressions multiplied by a random rate from 1% to 4% |
| `spend` | decimal | non-null | Rounded to two decimals; clicks multiplied by a random amount from 0.3 to 0.8 |
| `ctr` | decimal percent | non-null | `clicks / impressions * 100`, rounded to four decimals |
| `cpc` | decimal | non-null | `spend / clicks`, rounded to four decimals; zero if clicks are zero |
| `cpm` | decimal | non-null | `spend / impressions * 1000`, rounded to four decimals |
| `conversion` | integer | non-null | Integer truncation of clicks multiplied by a random rate from 1% to 5% |
| `conversion_rate` | decimal percent | non-null | `conversion / clicks * 100`, rounded to four decimals; zero if clicks are zero |

### Simulator limitations

- The report request, access token, pagination, account timezone, account currency, and
  API response envelope are not represented.
- Dimension IDs and names are independently randomized, so a repeated ID may receive
  different generated names across rows.
- Metrics are synthetic estimates and should not be treated as platform benchmarks.

\newpage

## 5. Google Analytics 4 Metadata

### Source contract

- Official Data API schema: [Google Analytics Data API dimensions and metrics](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
- GA4 configuration fields: [Google Analytics configuration reference](https://developers.google.com/analytics/devguides/collection/ga4/reference/config)
- Send login identity: [Send user IDs](https://developers.google.com/analytics/devguides/collection/ga4/user-id)
- Send hashed user data: [Send user-provided data using Measurement Protocol](https://developers.google.com/analytics/devguides/collection/ga4/uid-data)
- Measurement Protocol payload: [Measurement Protocol reference](https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference)
- PII policy: [Best practices to avoid sending PII](https://support.google.com/analytics/answer/6366371)

This dataset uses Google Analytics Data API dimension and metric names. It is a flat
CSV projection of synthetic report rows, not a GA4 BigQuery export event record.

### GA4 attribution and identity integration

#### UTM source and Facebook traffic

For a website, GA4 can read campaign parameters from the landing URL, including
`utm_source`, `utm_medium`, `utm_campaign`, `utm_id`, `utm_content`, and `utm_term`.
These values feed GA4 traffic-source dimensions such as `sessionSource`,
`sessionMedium`, `sessionCampaignName`, and `sessionCampaignId`.

`utm_source=facebook` identifies Facebook as the traffic source; it does not identify
the customer. Meta's `fbclid` is a click identifier, not a customer ID, `cid`, or
authenticated user identity. It must not be used as a CIR customer key. If the
application retains it for internal campaign diagnostics, do not place PII in campaign
parameters or page URLs, and apply the project's privacy and retention rules.

Do not confuse these identifiers: GA4 `client_id` identifies a browser or app instance,
GA4 `user_id` identifies a signed-in user with a non-PII site-owned value, `fbclid`
identifies a Meta ad click, and the simulator's `cid` identifies a synthetic customer.

The simulator now generates synthetic UTM fields and a Facebook-only `fbclid` as
collection metadata. These values represent landing-page attribution inputs; they are
not copied from a real URL or Facebook account.

#### Google SSO or internal login

After a successful Google SSO or internal login, the website may set GA4 `user_id` to a
stable, site-owned, pseudonymous identifier. Google documents `user_id` as a reserved
configuration value for connecting activity across sessions, devices, and platforms.
The value must not itself be PII. It must not be sent as a custom user property, a
custom dimension, or an event-level parameter.

Send `user_id` only while the user is authenticated. On logout, set it to `null`; do not
send an empty string or the text `"null"`. The simulator represents anonymous rows with
empty `user_id` and authenticated rows with a synthetic non-PII value. It also includes
synthetic `client_id`, `user_pseudo_id`, and `login_method` collection fields in the CSV.

#### Hashed email and phone

When a server-side integration uses GA4 Measurement Protocol, user-provided data can
be sent in the top-level `user_data` object alongside `user_id`. Email and phone are
not ordinary event parameters in this model. Measurement Protocol requires the
developer to normalize the values, hash them with SHA-256, and send lowercase
hex-encoded values in `sha256_email_address` and `sha256_phone_number`.

The documented normalization rules include:

- Email: remove leading/trailing whitespace, lowercase, remove spaces, and remove
  periods before the domain for `gmail.com` or `googlemail.com` addresses, then hash.
- Phone: remove all non-digit characters, add the `+` prefix using E.164-style form,
  then hash.

Only send this data when the required consent, privacy, and data-governance conditions
for the implementation are satisfied. Never send raw email or phone in a GA4 URL,
ordinary event parameter, or custom dimension. The simulator CSV contains only synthetic
SHA-256 fixture values in the `sha256_*` columns; it does not contain raw email or phone.

Illustrative Measurement Protocol shape:

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

The example values are placeholders. The actual request also requires the appropriate
Measurement Protocol transport credentials and identifiers.

### Dataset metadata

| Item | Value |
|---|---|
| File | `ga4_cir_api.csv` |
| Grain | One synthetic event/report row |
| Rows | `Config.NUM_ROWS` |
| Date field | `date` |
| Date format | `YYYYMMDD` |
| Event set | `page_view`, `view_item`, `begin_checkout`, `purchase` |
| Source set | `google`, `facebook`, `tiktok`, `zalo` |
| Revenue currency | Not explicitly declared; values are synthetic VND-like amounts |
| Key-event model | `purchase` produces `keyEvents = 1`; all other events produce `0` |
| Authentication model | Every third row is anonymous; other rows use synthetic `google_sso` or `internal` login context |
| UTM model | `utm_*` values are synthetic and aligned with the session/campaign fields |
| User data model | Authenticated rows contain synthetic SHA-256 email/phone values; raw PII is never generated |

### Column metadata

| Column | Type | Nullability | Meaning and generation |
|---|---|---|---|
| `date` | string date | non-null | Random date from base date through 10 days later, formatted `YYYYMMDD` |
| `eventName` | string enum-like | non-null | Random synthetic event from the four-event set |
| `campaignId` | string | non-null | Synthetic `GAD-C360-NNN` ID, random suffix from 1 to 4 |
| `campaignName` | string | non-null | Synthetic web campaign name, independently randomized from 1 to 4 |
| `sessionSource` | string enum-like | non-null | Random choice from `google`, `facebook`, `tiktok`, `zalo` |
| `sessionMedium` | string enum-like | non-null | Intended to be `cpc` for Google and `paid_social` otherwise; current implementation compares the source list rather than the selected source, so it currently emits `paid_social` for every row |
| `country` | string ISO-like code | non-null | Shared synthetic person's country, currently `VN` |
| `city` | string | non-null | Shared synthetic person's city |
| `browser` | string enum-like | non-null | Random choice from `Chrome`, `Safari`, `Edge` |
| `deviceCategory` | string enum-like | non-null | Random choice from `mobile`, `desktop`, `tablet` |
| `platform` | string enum-like | non-null | Fixed `web` |
| `sessionCampaignId` | string | non-null | Same generated value as `campaignId` |
| `sessionCampaignName` | string | non-null | Synthetic session campaign name; independently randomized from 1 to 4 |
| `transactionId` | string | empty for non-purchase | `ORD-NNNNN` for purchase rows; empty string otherwise |
| `eventCount` | integer | non-null | Random integer from 1 to 5 |
| `keyEvents` | integer | non-null | `1` for purchase rows and `0` otherwise; modern GA4 metric naming |
| `totalRevenue` | decimal | non-null | Random 450,000 to 950,000 for purchase rows; `0` otherwise |
| `engagementRate` | decimal fraction | non-null | Random fraction from 0.35 to 0.85; follows GA4 fraction convention |
| `client_id` | string | non-null | Synthetic browser/app collection identifier, unique per generated row; not a customer ID |
| `user_pseudo_id` | string | non-null | Synthetic pseudonymous GA4 collection identifier, unique per generated row |
| `user_id` | string | empty for anonymous rows | Synthetic non-PII site-owned identifier `GA4-CUST-NNNN` for authenticated rows |
| `login_method` | string | empty for anonymous rows | Synthetic authentication context: `google_sso` or `internal` |
| `utm_source` | string | non-null | Synthetic landing attribution source; equal to `sessionSource` |
| `utm_medium` | string | non-null | Synthetic landing attribution medium; equal to `sessionMedium`, `cpc` for Google and `paid_social` otherwise |
| `utm_campaign` | string | non-null | Synthetic landing campaign name; equal to `campaignName` and `sessionCampaignName` |
| `utm_id` | string | non-null | Synthetic landing campaign ID; equal to `campaignId` and `sessionCampaignId` |
| `fbclid` | string | empty except Facebook rows | Synthetic Meta click identifier when `utm_source` is `facebook`; never a customer key |
| `sha256_email_address` | string SHA-256 hex | empty for anonymous rows | Normalized synthetic email hashed as lowercase hexadecimal SHA-256 for Measurement Protocol-style user data |
| `sha256_phone_number` | string SHA-256 hex | empty for anonymous rows | Digits-only synthetic phone normalized with `+`, then hashed as lowercase hexadecimal SHA-256 |

### Simulator limitations

- `sessionSource` is not linked to a real session or user identifier.
- `sessionCampaignName` and `campaignName` are not guaranteed to correspond to their
  respective IDs.
- There is no `userPseudoId`, session ID, event timestamp, item array, user property,
  ecommerce item, or GA4 request/response envelope.
- `client_id`, `user_pseudo_id`, `user_id`, UTM fields, `fbclid`, and flattened
  `sha256_*` fields are simulator collection metadata; they are not all queryable as
  standard Google Analytics Data API dimensions.
- `totalRevenue` has no declared currency in this file.
- The `sessionMedium` implementation currently has a semantic defect documented above;
  consumers should not infer source-medium correctness from generated rows until fixed.

\newpage

## 6. Zalo Official Account Metadata

### Source contract

- Official documentation root: [Zalo Official Account API](https://developers.zalo.me/docs/api/official-account-api/)
- Get User Detail v3.0: [Zalo Get User Detail](https://developers.zalo.me/docs/official-account/quan-ly/quan-ly-thong-tin-nguoi-dung/lay-thong-tin-user)
- Endpoint represented: `/v3.0/oa/user/detail`

The current Zalo OA model is the v3.0 User Detail operation. A real response contains
nested shared-information data. This dataset flattens selected profile and shared-info
attributes into CSV columns.

### Dataset metadata

| Item | Value |
|---|---|
| File | `zalo_cir_api.csv` |
| Grain | One synthetic Zalo user profile |
| Rows | `Config.NUM_ROWS` |
| Primary source identifier | `user_id` |
| Identity fields | `display_name`, `shared_info_name`, `shared_info_phone`, `shared_info_city`, `shared_info_address` |
| Privacy/sensitivity fields | `is_sensitive`, `user_is_follower` |
| Flattening | `shared_info_*` columns represent values extracted from a real `shared_info` object |

### Column metadata

| Column | Type | Nullability | Meaning and generation |
|---|---|---|---|
| `user_id` | string integer | non-null | Synthetic Zalo user ID, base `567826391599986760` plus row index |
| `user_id_by_app` | string integer | non-null | Synthetic app-scoped user ID, base `567826390000000000` plus row index |
| `display_name` | string | non-null | Shared synthetic person's full name |
| `user_gender` | integer enum-like | non-null | Shared person's gender code; code comments define `1` male, `2` female, `0` other/unknown |
| `is_sensitive` | boolean | non-null | Fixed `False`; represents whether returned profile data is sensitive |
| `user_is_follower` | boolean | non-null | Fixed `True`; represents follower status |
| `shared_info_name` | string | non-null | Flattened name from the conceptual `shared_info` object |
| `shared_info_phone` | string phone | non-null | Flattened phone from the conceptual `shared_info` object; CIR identity field |
| `shared_info_city` | string | non-null | Flattened city from the conceptual `shared_info` object |
| `shared_info_address` | string | non-null | Synthetic `Sample street N, city` address |
| `tags_and_notes_info` | string enum-like | non-null | Synthetic label from four Vietnamese-language tag values; not a documented profile identity field |

### Simulator limitations

- The CSV is not the raw JSON response and does not preserve the `shared_info` object.
- `tags_and_notes_info` is simulator-specific enrichment, not part of the documented
  user identity projection described here.
- Real Zalo permissions, access tokens, follower authorization, privacy behavior, and
  response error envelopes are not represented.
- Zalo documentation is rendered through a JS-heavy portal; verify exact response
  fields against live v3.0 response examples before treating this CSV as an integration
  fixture.

\newpage

## 7. AppsFlyer Pull API Raw-Data Metadata

### Source contract

- Pull API raw data: [AppsFlyer Pull API raw data](https://support.appsflyer.com/hc/en-us/articles/360007530258-Pull-API-raw-data)
- Raw data field dictionary: [AppsFlyer raw data field dictionary](https://support.appsflyer.com/hc/en-us/articles/208387843-Raw-data-field-dictionary)

AppsFlyer raw-data reports use a broad, report-dependent schema. Fields can be empty
or unavailable depending on report type, platform, attribution source, privacy settings,
and delivery method. This simulator models an in-app-event report with a selected set
of canonical Pull API field names.

### Dataset metadata

| Item | Value |
|---|---|
| File | `appsflyer_cir_api.csv` |
| Grain | One synthetic AppsFlyer in-app event |
| Rows | `Config.NUM_ROWS` |
| Identity fields | `customer_user_id`, JSON `event_value.customer_id`, device identifiers |
| Event set | `af_login`, `af_content_view`, `af_add_to_cart`, `af_purchase` |
| Time format | `YYYY-MM-DD HH:MM:SS` without an explicit timezone |
| Event value format | JSON string; purchase rows include `customer_id` and `order_id` |
| Revenue currency | Fixed `VND` for rows with synthetic revenue |

### Column metadata

| Column | Type | Nullability | Meaning and generation |
|---|---|---|---|
| `attributed_touch_type` | string enum-like | non-null | Random `click` or `impression`; attribution engagement type |
| `attributed_touch_time` | string datetime | non-null | Install time minus a random 5 to 60 minutes |
| `install_time` | string datetime | non-null | Base date plus random 0 to 5 days and 1 to 12 hours |
| `event_time` | string datetime | non-null | Install time plus random 5 to 120 minutes |
| `event_name` | string enum-like | non-null | Random AppsFlyer event name from the four-event set |
| `event_value` | string JSON | non-null | JSON containing `customer_id`; purchase rows also contain `order_id` |
| `event_revenue` | decimal | non-null | Random 100,000 to 500,000 for purchase rows; `0` otherwise |
| `event_revenue_currency` | string currency code | non-null | Fixed `VND` |
| `event_source` | string enum-like | non-null | Fixed `SDK` |
| `media_source` | string enum-like | non-null | Random `facebook`, `tiktok`, `googleadwords_int`, or `zalo` |
| `channel` | string | non-null | Random display label: `Facebook Ads`, `TikTok Ads`, `Google Ads`, or `Zalo` |
| `campaign` | string | non-null | Synthetic campaign name; random suffix from 1 to 4 |
| `campaign_id` | string | non-null | Synthetic AppsFlyer campaign ID `AF-CAMP-NNN` |
| `adset` | string | non-null | Synthetic ad set name; random suffix from 1 to 5 |
| `adset_id` | string | non-null | Synthetic ad set ID `AF-AS-NNN` |
| `ad` | string | non-null | Synthetic creative name; random suffix from 1 to 7 |
| `ad_id` | string | non-null | Synthetic ad ID `AF-AD-NNN` |
| `country_code` | string ISO-like code | non-null | Shared person's country, currently `VN` |
| `state` | string | non-null | Shared person's state/province |
| `city` | string | non-null | Shared person's city |
| `postal_code` | string | non-null | Synthetic six-digit-looking postal code from 700010 to 700099 |
| `ip` | string IPv4-like | non-null | Synthetic IPv4-like address beginning `103.21.` |
| `operator` | string | non-null | Random `Viettel`, `MobiFone`, or `VinaPhone`; AppsFlyer field dictionary name used by current code |
| `language` | string BCP-47-like | non-null | Fixed `vi` |
| `appsflyer_id` | string | non-null | Synthetic SDK-generated-looking ID; random two-digit segment means uniqueness is not guaranteed |
| `advertising_id` | string UUID-like | non-null | Synthetic resettable advertising ID; row-index suffix makes this simulator-unique |
| `idfa` | string | empty on Android | Synthetic iOS advertising identifier; empty when `is_android` is true |
| `android_id` | string | empty on iOS | Synthetic Android identifier; empty when `is_android` is false |
| `customer_user_id` | string | non-null | Shared synthetic `cid`, strongest AppsFlyer-to-CIR key |
| `idfv` | string | empty on Android | Synthetic iOS vendor identifier; empty when `is_android` is true |
| `platform` | string enum-like | non-null | `android` or `ios`, selected by `is_android` |
| `device_type` | string | non-null | Synthetic device model label; AppsFlyer field dictionary notes this field is deprecated/unpopulated in favor of device model in current reports |
| `os_version` | string | non-null | Random Android `14`/`15` or iOS `17.6`/`16.7` |
| `app_version` | string | non-null | Fixed `6.8.1` |
| `sdk_version` | string | non-null | Fixed `6.15.0` |
| `app_id` | string | non-null | Fixed synthetic app ID `com.example.customer360` |
| `bundle_id` | string | non-null | Fixed synthetic bundle/app ID `com.example.customer360` |
| `user_agent` | string | non-null | Synthetic Android or iOS browser-style user agent |
| `http_referrer` | string URL | non-null | Fixed synthetic landing URL |
| `original_url` | string URL | non-null | Synthetic app URL containing the person's `cid` |

### Simulator limitations

- This is a selected subset of the AppsFlyer main schema, not a complete raw-data field
  dictionary export.
- The timestamp strings omit timezone information even though delivery method and account
  settings affect real AppsFlyer timestamp timezone behavior.
- Attribution hierarchy values are independently randomized and are not a stable campaign
  entity model.
- `appsflyer_id` can collide because only a limited random suffix is used; do not use it
  as a guaranteed unique key in tests.
- Device identifiers are synthetic and must not be interpreted as real device identity.

## 8. Cross-Platform CIR Metadata

### Identity key availability

| Platform | Strongest shared key | Secondary keys | Direct `cid` column? |
|---|---|---|---|
| Meta | `field_email`, `field_phone` | First/last name, city, state | No |
| TikTok | None | Advertiser/campaign hierarchy and country only | No |
| GA4 | None in current schema; production GA4 may use non-PII `user_id` | City, country, campaign/source context, optional UTM-derived traffic dimensions | No |
| Zalo | `shared_info_phone` | Name, city, address | No |
| AppsFlyer | `customer_user_id`, `event_value.customer_id` | Original URL, device identifiers, geography | Yes, embedded in fields rather than as a column |

### Expected synthetic row-to-person mapping

- Meta, GA4, Zalo, and AppsFlyer select shared people cyclically.
- With `20` rows and `8` people, person positions repeat after every eight rows.
- TikTok rows are aggregate advertising observations and intentionally have no person-level
  identity.
- A real CIR implementation should normalize email and phone before matching and should
  treat names, city, and device identifiers as supporting evidence rather than guaranteed
  unique identifiers.

### Data quality and reproducibility

- The module seeds Python's global random generator with `42` at import time.
- Importing this module has side effects: it creates the configured output directory and
  changes the process-global random sequence.
- Reproducibility assumes the same Python implementation, execution order, configuration,
  and generator code.
- CSV values are serialized from Python values; downstream ingestion should explicitly cast
  numeric, boolean, date, datetime, and JSON-string fields instead of relying on inference.

## 9. 
