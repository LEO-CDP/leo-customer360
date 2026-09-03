import csv
import json
import os
from collections import defaultdict

# =====================================================================
# 1. UNION-FIND (DISJOINT SET) GRAPH
# The algorithmic core for deterministic identity stitching.
# This logic is what you would eventually scale into a graph database 
# like ArangoDB or Apache AGE for enterprise production.
# =====================================================================
class IdentityGraph:
    def __init__(self):
        # Maps an identifier to its parent identifier
        self.parent = {}
        # Stores the actual records/events attached to each identifier
        self.records = defaultdict(list)

    def find(self, node):
        """Finds the root identity for a given node with path compression."""
        if self.parent.setdefault(node, node) == node:
            return node
        self.parent[node] = self.find(self.parent[node])
        return self.parent[node]

    def union(self, node1, node2):
        """Merges two identity nodes into the same unified profile."""
        root1 = self.find(node1)
        root2 = self.find(node2)
        if root1 != root2:
            self.parent[root1] = root2

    def add_record(self, node, platform, record):
        """Attaches a raw data record to a specific identifier."""
        self.records[node].append({
            "platform": platform,
            "data": record
        })

# =====================================================================
# 2. DATA LOADERS & STITCHING LOGIC
# =====================================================================
DIR_PATH = "./all-data-simulator/platform_cir_csv"

def load_csv(filename):
    path = os.path.join(DIR_PATH, filename)
    if not os.path.exists(path):
        return []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def build_identity_graph():
    graph = IdentityGraph()

    # ---------------------------------------------------------
    # A. SIMULATED CRM BRIDGE (CUID <-> PII)
    # In reality, this comes from your Postgres/backend DB.
    # We reconstruct it here to bridge Adjust/GA4 with Meta/Zalo.
    # ---------------------------------------------------------
    crm_users = [
        {"cuid": "CUST-0001", "email": "an.nguyen@example.test", "phone": "84901234001"},
        {"cuid": "CUST-0002", "email": "binh.tran@example.test", "phone": "84901234002"},
        {"cuid": "CUST-0003", "email": "chi.le@example.test", "phone": "84901234003"},
        {"cuid": "CUST-0004", "email": "dung.pham@example.test", "phone": "84901234004"},
        {"cuid": "CUST-0005", "email": "giang.vo@example.test", "phone": "84901234005"},
        {"cuid": "CUST-0006", "email": "hanh.do@example.test", "phone": "84901234006"},
        {"cuid": "CUST-0007", "email": "khoa.bui@example.test", "phone": "84901234007"},
        {"cuid": "CUST-0008", "email": "linh.hoang@example.test", "phone": "84901234008"},
    ]
    
    for user in crm_users:
        id_cuid = f"CUID:{user['cuid']}"
        id_email = f"EMAIL:{user['email']}"
        id_phone = f"PHONE:{user['phone']}"
        
        # Stitch them together
        graph.union(id_cuid, id_email)
        graph.union(id_cuid, id_phone)

    # ---------------------------------------------------------
    # B. META LEAD ADS
    # Primary join keys: Email, Phone
    # ---------------------------------------------------------
    meta_data = load_csv("meta_cir_api.csv")
    for row in meta_data:
        email = row.get("field_email")
        phone = row.get("field_phone")
        meta_id = row.get("id")
        
        if email and phone:
            id_email = f"EMAIL:{email}"
            id_phone = f"PHONE:{phone}"
            id_meta = f"META:{meta_id}"
            
            # Stitch keys and attach record
            graph.union(id_email, id_phone)
            graph.union(id_phone, id_meta)
            graph.add_record(id_meta, "Meta Lead Ads", row)

    # ---------------------------------------------------------
    # C. ZALO OA
    # Primary join keys: Phone
    # ---------------------------------------------------------
    zalo_data = load_csv("zalo_cir_api.csv")
    for row in zalo_data:
        phone = row.get("shared_info_phone")
        zalo_id = row.get("user_id")
        
        if phone:
            id_phone = f"PHONE:{phone}"
            id_zalo = f"ZALO:{zalo_id}"
            
            graph.union(id_phone, id_zalo)
            graph.add_record(id_zalo, "Zalo OA", row)

    # ---------------------------------------------------------
    # D. ADJUST
    # Primary join keys: CUID, Transaction ID (from event_value JSON)
    # ---------------------------------------------------------
    adjust_data = load_csv("adjust_cir_api.csv")
    for row in adjust_data:
        cuid = row.get("customer_user_id")
        adjust_id = row.get("adjust_id")
        id_cuid = f"CUID:{cuid}"
        id_adjust = f"ADJ:{adjust_id}"
        
        graph.union(id_cuid, id_adjust)
        
        # Extract nested Transaction ID if it's a purchase
        try:
            event_val = json.loads(row.get("event_value", "{}"))
            txn_id = event_val.get("order_id")
            if txn_id:
                id_txn = f"TXN:{txn_id}"
                graph.union(id_cuid, id_txn)
        except json.JSONDecodeError:
            pass
            
        graph.add_record(id_adjust, "Adjust", row)

    # ---------------------------------------------------------
    # E. GA4
    # Primary join keys: Transaction ID
    # ---------------------------------------------------------
    ga4_data = load_csv("ga4_cir_api.csv")
    for row in ga4_data:
        txn_id = row.get("transactionId")
        if txn_id:
            id_txn = f"TXN:{txn_id}"
            # GA4 row is attached to the transaction identifier
            graph.add_record(id_txn, "GA4", row)

    return graph

# =====================================================================
# 3. PROFILE AGGREGATION
# Group all dispersed records under their resolved root identity.
# =====================================================================
def generate_unified_profiles(graph):
    profiles = defaultdict(lambda: {
        "identifiers": set(),
        "timeline": []
    })
    
    # 1. Group all known identifiers to their root parent
    for node in list(graph.parent.keys()):
        root = graph.find(node)
        profiles[root]["identifiers"].add(node)
        
    # 2. Collect and append the attached records
    for node, records in graph.records.items():
        root = graph.find(node)
        profiles[root]["timeline"].extend(records)
        
    return profiles

# =====================================================================
# MAIN EXECUTION
# =====================================================================
if __name__ == "__main__":
    print("Building Identity Graph...")
    identity_graph = build_identity_graph()
    
    print("Resolving Unified Profiles...")
    resolved_profiles = generate_unified_profiles(identity_graph)
    
    # Filter and display a clean summary of stitched profiles
    valid_profiles = {k: v for k, v in resolved_profiles.items() if v["timeline"]}
    
    print(f"\nSuccessfully resolved {len(valid_profiles)} unified profiles.\n")
    print("=" * 60)
    
    # Print the first 2 stitched profiles as an example
    for root_id, profile_data in list(valid_profiles.items())[:2]:
        print(f"PROFILE ROOT: {root_id}")
        
        # Sort identifiers for readable output
        ids = sorted(list(profile_data["identifiers"]))
        print("IDENTIFIERS FOUND:")
        for ident in ids:
            print(f"  - {ident}")
            
        print(f"\nCROSS-PLATFORM EVENTS ({len(profile_data['timeline'])} total):")
        
        # Summarize the events rather than printing the huge raw dicts
        platform_counts = defaultdict(int)
        for event in profile_data["timeline"]:
            platform_counts[event["platform"]] += 1
            
        for plat, count in platform_counts.items():
            print(f"  - {plat}: {count} records stitched")
            
        print("=" * 60)