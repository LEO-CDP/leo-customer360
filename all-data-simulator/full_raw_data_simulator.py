import csv
import os
import zipfile
import random
from datetime import datetime, timedelta

# =====================================================================
# CONFIGURATION
# =====================================================================
class Config:
    NUM_ROWS = 20
    OUT_DIR = "./all-data-simulator/platform_cir_csv"
    ZIP_PATH = "./all-data-simulator/platform_cir_api_csv_simulated.zip"
    BASE_DATE = datetime(2026, 8, 1, 9, 0, 0)
    SEED = 42 # Ensures generated data is identical across runs for CIR testing

# Seed random for deterministic outputs
random.seed(Config.SEED)
os.makedirs(Config.OUT_DIR, exist_ok=True)

# =====================================================================
# SHARED SYNTHETIC IDENTITIES
# Recurring identities across platforms to allow CIR matching
# =====================================================================
PEOPLE = [
    {"cid": "CUST-0001", "name": "Nguyen An", "first": "An", "last": "Nguyen", "email": "an.nguyen@example.test", "phone": "84901234001", "city": "Ho Chi Minh City", "state": "Ho Chi Minh", "country": "VN"},
    {"cid": "CUST-0002", "name": "Tran Binh", "first": "Binh", "last": "Tran", "email": "binh.tran@example.test", "phone": "84901234002", "city": "Hanoi", "state": "Hanoi", "country": "VN"},
    {"cid": "CUST-0003", "name": "Le Chi", "first": "Chi", "last": "Le", "email": "chi.le@example.test", "phone": "84901234003", "city": "Da Nang", "state": "Da Nang", "country": "VN"},
    {"cid": "CUST-0004", "name": "Pham Dung", "first": "Dung", "last": "Pham", "email": "dung.pham@example.test", "phone": "84901234004", "city": "Can Tho", "state": "Can Tho", "country": "VN"},
    {"cid": "CUST-0005", "name": "Vo Giang", "first": "Giang", "last": "Vo", "email": "giang.vo@example.test", "phone": "84901234005", "city": "Hai Phong", "state": "Hai Phong", "country": "VN"},
    {"cid": "CUST-0006", "name": "Do Hanh", "first": "Hanh", "last": "Do", "email": "hanh.do@example.test", "phone": "84901234006", "city": "Nha Trang", "state": "Khanh Hoa", "country": "VN"},
    {"cid": "CUST-0007", "name": "Bui Khoa", "first": "Khoa", "last": "Bui", "email": "khoa.bui@example.test", "phone": "84901234007", "city": "Bien Hoa", "state": "Dong Nai", "country": "VN"},
    {"cid": "CUST-0008", "name": "Hoang Linh", "first": "Linh", "last": "Hoang", "email": "linh.hoang@example.test", "phone": "84901234008", "city": "Vung Tau", "state": "Ba Ria-Vung Tau", "country": "VN"},
]

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================
def write_csv(filename, fieldnames, rows):
    path = os.path.join(Config.OUT_DIR, filename)
    # utf-8-sig adds BOM for correct Vietnamese character rendering in Excel
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path

# =====================================================================
# 1) META LEAD ADS (Graph API)
# Expanded to include form_id and name hierarchy as returned by the API
# =====================================================================
def generate_meta_data(num_rows):
    fields = [
        "lead_id", "created_time", "form_id", 
        "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
        "field_email", "field_phone", "field_first_name", "field_last_name",
        "field_city", "field_state", "field_country"
    ]
    rows = []
    for i in range(num_rows):
        p = PEOPLE[i % len(PEOPLE)]
        ts = Config.BASE_DATE + timedelta(hours=i*4, minutes=random.randint(0, 59))
        rows.append({
            "lead_id": f"meta_lead_{i+1:08d}",
            "created_time": ts.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "form_id": "890000123456789",
            "campaign_id": f"120990000000{random.randint(1, 4)}",
            "campaign_name": f"Meta_Acquisition_Camp_{random.randint(1, 4)}",
            "adset_id": f"238770000000{random.randint(1, 5)}",
            "adset_name": "Broad_Audience_18_65",
            "ad_id": f"639880000000{random.randint(1, 7)}",
            "ad_name": f"Video_Creative_V{random.randint(1, 7)}",
            "field_email": p["email"],
            "field_phone": p["phone"],
            "field_first_name": p["first"],
            "field_last_name": p["last"],
            "field_city": p["city"],
            "field_state": p["state"],
            "field_country": p["country"],
        })
    return fields, rows

# =====================================================================
# 2) TIKTOK MARKETING API (Reporting)
# Refined field names to strictly match TikTok Integrated Reports API
# =====================================================================
def generate_tiktok_data(num_rows):
    fields = [
        "advertiser_id", "stat_time_day", "campaign_id", "campaign_name",
        "adgroup_id", "adgroup_name", "ad_id", "ad_name", "country_code",
        "impressions", "clicks", "spend", "ctr", "cpc", "cpm",
        "conversion", "conversion_rate"
    ]
    rows = []
    countries = ["VN", "VN", "VN", "VN", "SG", "TH"]
    for i in range(num_rows):
        day = (Config.BASE_DATE + timedelta(days=i % 10)).strftime("%Y-%m-%d")
        impressions = random.randint(10000, 20000)
        clicks = int(impressions * random.uniform(0.01, 0.04))
        spend = round(clicks * random.uniform(0.3, 0.8), 2)
        conversions = int(clicks * random.uniform(0.01, 0.05))
        
        rows.append({
            "advertiser_id": "710000123456789",
            "stat_time_day": day,
            "campaign_id": f"723400000000{random.randint(1,4)}",
            "campaign_name": f"TT_C360_Acquisition_{random.randint(1,4)}",
            "adgroup_id": f"723410000000{random.randint(1,5)}",
            "adgroup_name": f"VN_Prospecting_{random.randint(1,5)}",
            "ad_id": f"723420000000{random.randint(1,7)}",
            "ad_name": f"UGC_Creator_{random.randint(1,7)}",
            "country_code": random.choice(countries),
            "impressions": impressions,
            "clicks": clicks,
            "spend": spend,
            "ctr": round((clicks / impressions) * 100, 4) if impressions else 0,
            "cpc": round(spend / clicks, 4) if clicks else 0,
            "cpm": round((spend / impressions) * 1000, 4) if impressions else 0,
            "conversion": conversions,
            "conversion_rate": round((conversions / clicks) * 100, 4) if clicks else 0,
        })
    return fields, rows

# =====================================================================
# 3) GA4 (Google Analytics Data API)
# Updated source/medium to sessionSource/sessionMedium to align with 
# official GA4 Data API dimensional schemas.
# =====================================================================
def generate_ga4_data(num_rows):
    fields = [
        "date", "eventName", "campaignId", "campaignName", "sessionSource", 
        "sessionMedium", "country", "city", "browser", "deviceCategory", 
        "platform", "sessionCampaignId", "sessionCampaignName", "transactionId",
        "eventCount", "conversions", "totalRevenue", "engagementRate"
    ]
    rows = []
    events = ["page_view", "view_item", "begin_checkout", "purchase"]
    sources = ["google", "facebook", "tiktok", "zalo"]
    
    for i in range(num_rows):
        p = PEOPLE[i % len(PEOPLE)]
        dt = Config.BASE_DATE.date() + timedelta(days=random.randint(0, 10))
        campaign_id = f"GAD-C360-{random.randint(1,4):03d}"
        event_name = random.choice(events)
        
        is_purchase = event_name == "purchase"
        txid = f"ORD-{i+1:05d}" if is_purchase else ""
        conversions = 1 if is_purchase else 0
        revenue = round(random.uniform(450000, 950000), 2) if is_purchase else 0
        
        rows.append({
            "date": dt.strftime("%Y-%m-%d"),
            "eventName": event_name,
            "campaignId": campaign_id,
            "campaignName": f"C360_Web_{random.randint(1,4)}",
            "sessionSource": random.choice(sources),
            "sessionMedium": "paid_social" if sources != "google" else "cpc",
            "country": p["country"],
            "city": p["city"],
            "browser": random.choice(["Chrome", "Safari", "Edge"]),
            "deviceCategory": random.choice(["mobile", "desktop", "tablet"]),
            "platform": "web",
            "sessionCampaignId": campaign_id,
            "sessionCampaignName": f"C360_Web_{random.randint(1,4)}",
            "transactionId": txid,
            "eventCount": random.randint(1, 5),
            "conversions": conversions,
            "totalRevenue": revenue,
            "engagementRate": round(random.uniform(0.35, 0.85), 4),
        })
    return fields, rows

# =====================================================================
# 4) ZALO OA API (oa/getprofile endpoint)
# Mapped 'shared_*' to 'shared_info_*' to represent Zalo's nested 
# JSON structure, and added user_is_follower.
# =====================================================================
def generate_zalo_data(num_rows):
    fields = [
        "user_id", "user_id_by_app", "display_name", "user_gender",
        "is_sensitive", "user_is_follower", "shared_info_name", 
        "shared_info_phone", "shared_info_city", "shared_info_address", 
        "tags_and_notes_info"
    ]
    rows = []
    tags = ["Khach hang moi", "Khach VIP", "Quan tam san pham", "Da mua hang"]
    
    for i in range(num_rows):
        p = PEOPLE[i % len(PEOPLE)]
        rows.append({
            "user_id": str(567826391599986760 + i),
            "user_id_by_app": str(567826390000000000 + i),
            "display_name": p["name"],
            "user_gender": 0,   # 0=Male, 1=Female, 2=Unknown (Zalo standard)
            "is_sensitive": False,
            "user_is_follower": True,
            "shared_info_name": p["name"],
            "shared_info_phone": p["phone"],
            "shared_info_city": p["city"],
            "shared_info_address": f"Sample street {random.randint(1,99)}, {p['city']}",
            "tags_and_notes_info": random.choice(tags),
        })
    return fields, rows

# =====================================================================
# 5) APPSFLYER RAW DATA (Pull API)
# Highly accurate to AF Pull API schema. Minor randomization updates.
# =====================================================================
def generate_appsflyer_data(num_rows):
    fields = [
        "attributed_touch_type", "attributed_touch_time", "install_time",
        "event_time", "event_name", "event_value", "event_revenue",
        "event_revenue_currency", "event_source", "media_source", "channel",
        "campaign", "campaign_id", "adset", "adset_id", "ad", "ad_id",
        "country_code", "state", "city", "postal_code", "ip", "carrier",
        "language", "appsflyer_id", "advertising_id", "idfa", "android_id",
        "customer_user_id", "idfv", "platform", "device_type", "os_version",
        "app_version", "sdk_version", "app_id", "bundle_id", "user_agent",
        "http_referrer", "original_url"
    ]
    rows = []
    media_sources = ["facebook", "tiktok", "googleadwords_int", "zalo"]
    events = ["af_login", "af_content_view", "af_add_to_cart", "af_purchase"]
    
    for i in range(num_rows):
        p = PEOPLE[i % len(PEOPLE)]
        install_ts = Config.BASE_DATE + timedelta(days=random.randint(0,5), hours=random.randint(1,12))
        event_ts = install_ts + timedelta(minutes=random.randint(5, 120))
        
        event_name = random.choice(events)
        is_purchase = event_name == "af_purchase"
        revenue = round(random.uniform(100000, 500000), 2) if is_purchase else 0
        
        is_android = random.choice([True, False])
        
        rows.append({
            "attributed_touch_type": random.choice(["click", "impression"]),
            "attributed_touch_time": (install_ts - timedelta(minutes=random.randint(5, 60))).strftime("%Y-%m-%d %H:%M:%S"),
            "install_time": install_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "event_time": event_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "event_name": event_name,
            "event_value": f'{{"customer_id":"{p["cid"]}","order_id":"ORD-{i+1:05d}"}}' if is_purchase else f'{{"customer_id":"{p["cid"]}"}}',
            "event_revenue": revenue,
            "event_revenue_currency": "VND",
            "event_source": "SDK",
            "media_source": random.choice(media_sources),
            "channel": random.choice(["Facebook Ads", "TikTok Ads", "Google Ads", "Zalo"]),
            "campaign": f"C360_App_{random.randint(1,4)}",
            "campaign_id": f"AF-CAMP-{random.randint(1,4):03d}",
            "adset": f"AdSet_{random.randint(1,5)}",
            "adset_id": f"AF-AS-{random.randint(1,5):03d}",
            "ad": f"Creative_{random.randint(1,7)}",
            "ad_id": f"AF-AD-{random.randint(1,7):03d}",
            "country_code": p["country"],
            "state": p["state"],
            "city": p["city"],
            "postal_code": f"{700000 + random.randint(10, 99)}",
            "ip": f"103.21.{random.randint(10, 50)}.{random.randint(20, 99)}",
            "carrier": random.choice(["Viettel", "MobiFone", "VinaPhone"]),
            "language": "vi",
            "appsflyer_id": f"1740000000{random.randint(10,99)}-a1b2c3d4e5f6",
            "advertising_id": f"aa000000-1111-2222-3333-{i:012d}",
            "idfa": "" if is_android else f"IDFA-{i+1:012d}",
            "android_id": f"ANDROID-{i+1:012d}" if is_android else "",
            "customer_user_id": p["cid"],
            "idfv": "" if is_android else f"IDFV-{i+1:012d}",
            "platform": "android" if is_android else "ios",
            "device_type": random.choice(["Samsung Galaxy S24", "Google Pixel 9"]) if is_android else random.choice(["iPhone 15", "iPhone 14"]),
            "os_version": random.choice(["14", "15"]) if is_android else random.choice(["17.6", "16.7"]),
            "app_version": "6.8.1",
            "sdk_version": "6.15.0",
            "app_id": "com.example.customer360",
            "bundle_id": "com.example.customer360",
            "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9)" if is_android else "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X)",
            "http_referrer": "https://example.test/landing",
            "original_url": f"https://example.test/app?cid={p['cid']}",
        })
    return fields, rows

# =====================================================================
# MAIN EXECUTION
# =====================================================================
if __name__ == "__main__":
    datasets = {
        "meta_cir_api.csv": generate_meta_data(Config.NUM_ROWS),
        "tiktok_cir_api.csv": generate_tiktok_data(Config.NUM_ROWS),
        "ga4_cir_api.csv": generate_ga4_data(Config.NUM_ROWS),
        "zalo_cir_api.csv": generate_zalo_data(Config.NUM_ROWS),
        "appsflyer_cir_api.csv": generate_appsflyer_data(Config.NUM_ROWS),
    }

    generated_paths = []
    
    # Write individual CSVs
    for filename, (fields, rows) in datasets.items():
        path = write_csv(filename, fields, rows)
        generated_paths.append(path)

    # Bundle into ZIP for convenience
    with zipfile.ZipFile(Config.ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in generated_paths:
            z.write(p, arcname=os.path.basename(p))

    print(f"Data Simulation Complete ({Config.NUM_ROWS} rows per platform).")
    print("Files created in:", Config.OUT_DIR)
    for p in generated_paths:
        print(f"  - {os.path.basename(p)}")
    print(f"\nBundled Archive: {Config.ZIP_PATH}")