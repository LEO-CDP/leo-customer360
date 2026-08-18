#!/usr/bin/env python3
"""
Read-only catalog discovery for GreenNode/VNG Cloud vServer.

Lists the exact names this account offers for the Terraform inputs that are
account/zone-specific: flavor_zone_name, the flavor names, volume_type_zone_name,
root_disk_type_name, and image_name (matched on the image's `imageVersion`).

Usage (run from deployments/server):
    python discover-catalog.py [uat|prod]

Creds are read from .env (TF_VAR_client_id/secret) or terraform.tfvars.
project_id is read from overlays/<env>.tfvars. Nothing is written or changed.
"""
import base64, json, os, re, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV  = sys.argv[1] if len(sys.argv) > 1 else "uat"

TARGET_FLAVORS = {"s2-general-4x8", "s2-general-8x16"}

def from_tfvars(path, key):
    if not os.path.exists(path):
        return ""
    for line in open(path, encoding="utf-8"):
        m = re.match(r'\s*%s\s*=\s*"([^"]+)"' % re.escape(key), line)
        if m:
            return m.group(1)
    return ""

def creds():
    cid  = os.environ.get("TF_VAR_client_id", "")
    csec = os.environ.get("TF_VAR_client_secret", "")
    envf = os.path.join(HERE, ".env")
    if os.path.exists(envf):
        for line in open(envf, encoding="utf-8"):
            if line.startswith("TF_VAR_client_id="):     cid  = cid  or line.split("=",1)[1].strip()
            if line.startswith("TF_VAR_client_secret="):  csec = csec or line.split("=",1)[1].strip()
    tfv = os.path.join(HERE, "terraform.tfvars")
    cid  = cid  or from_tfvars(tfv, "client_id")
    csec = csec or from_tfvars(tfv, "client_secret")
    return cid, csec

def get_token(cid, csec, token_url):
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "email"}).encode()
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    req = urllib.request.Request(token_url, data=body, method="POST", headers={
        "Authorization": "Basic " + basic,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    return d.get("access_token") or d.get("accessToken") or ""

def api(base, token, path):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main():
    cid, csec = creds()
    if not cid or not csec:
        sys.exit("ERROR: client_id/secret not found in .env or terraform.tfvars")
    ovl       = os.path.join(HERE, "overlays", f"{ENV}.tfvars")
    project   = os.environ.get("TF_VAR_project_id") or from_tfvars(ovl, "project_id")
    zone      = from_tfvars(ovl, "zone_id") or "?"
    token_url = os.environ.get("TF_VAR_token_url",       "https://iamapis.vngcloud.vn/accounts-api/v2/auth/token")
    base      = os.environ.get("TF_VAR_vserver_base_url", "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway")
    if not project:
        sys.exit(f"ERROR: project_id not found in {ovl}")

    print(f"env={ENV}  project={project}  zone(overlay)={zone}")
    print("Authenticating...")
    token = get_token(cid, csec, token_url)
    if not token:
        sys.exit("ERROR: could not obtain access token (check credentials).")
    print("OK. Querying catalog...\n")

    # 1) Flavor zones + the flavors inside each (find where s2-general-* live).
    print("===== FLAVOR ZONES  ->  set flavor_zone_name =====")
    fz = api(base, token, f"/v1/{project}/flavor_zones/product").get("flavorZones", [])
    for z in fz:
        zid, zname = z.get("id"), z.get("name")
        try:
            flavors = api(base, token, f"/v1/{project}/{zid}/flavors").get("flavors", [])
        except Exception as e:
            flavors = []
            note = f"  (flavors lookup failed: {e})"
        else:
            note = ""
        fnames = [f.get("name") for f in flavors]
        hits = sorted(TARGET_FLAVORS.intersection(fnames))
        star = "  <== has s2-general-4x8/8x16" if hits else ""
        print(f"\n  flavor_zone_name = {zname!r}   (id={zid}){star}{note}")
        if fnames:
            for n in sorted(filter(None, fnames)):
                mark = " *" if n in TARGET_FLAVORS else ""
                print(f"       flavor: {n}{mark}")

    # 2) Volume type zones + their disk types (find the SSD root-disk type).
    print("\n===== VOLUME TYPE ZONES  ->  set volume_type_zone_name / root_disk_type_name =====")
    vtz = api(base, token, f"/v1/{project}/volume_type_zones").get("volumeTypeZones", [])
    for z in vtz:
        zid, zname = z.get("id"), z.get("name")
        try:
            vts = api(base, token, f"/v1/{project}/{zid}/volume_types").get("volumeTypes", [])
        except Exception as e:
            vts = []
        print(f"\n  volume_type_zone_name = {zname!r}   (id={zid})")
        for v in vts:
            print(f"       root_disk_type_name = {v.get('name')!r}   (iops={v.get('iops')} min={v.get('minSize')} max={v.get('maxSize')})")

    # 3) OS images (image_name is matched on imageVersion; must be offered in the flavor zone).
    print("\n===== OS IMAGES  ->  set image_name (matched on imageVersion) =====")
    imgs = api(base, token, f"/v1/{project}/images/os").get("images", [])
    ubuntu = [i for i in imgs if "ubuntu" in (i.get("imageVersion","") + i.get("imageType","")).lower()]
    for i in (ubuntu or imgs):
        print(f"       image_name = {i.get('imageVersion')!r}   (type={i.get('imageType')} id={i.get('id')} flavorZoneIds={i.get('flavorZoneIds')})")
    if ubuntu:
        print("\n  NOTE: pick the image whose flavorZoneIds includes the id of your chosen flavor_zone_name above.")

if __name__ == "__main__":
    main()
