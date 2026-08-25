# UAT overlay — small utility/API box that also reaches the private vDB (HCM03-1C).
# Smallest flavor + a floating (public) IP for SSH; cloud-init installs psql so you
# can run SQL against the DB's PRIVATE ip from this box.
# Secrets (client_id/secret, ssh_public_key, user_password) live in ../terraform.tfvars or ../.env.
# Apply with:  ./deploy.sh uat <plan|apply>   (Terraform workspace "uat").

name_prefix = "c360-api-uat"

# Tracing: persist OpenTelemetry ON for uat so it survives redeploys (read by deploy-api.sh ->
# lib/otel.sh). Without this the uat default is OFF (tiny-box zero-overhead); an explicit
# OTEL_ENABLED env var at deploy time still overrides. See deployments/monitoring (Jaeger).
otel_enabled = "true"

servers = {
  "1x2" = {
    flavor_name    = "s-general-1x2" # 1 vCPU / 2 GB — jump host + backend-system (Dagster)
    root_disk_size = 20
    name           = "backend" # -> c360-api-uat-backend
  }
  "api" = {
    # Kept at 1x2 for now. A resize to s-general-2x4 (2 vCPU / 4 GB) is available and is an in-place
    # terraform change (0 destroy) but reboots the box + costs more — revisit if 2 GB gets tight
    # (this box runs api + keycloak + redis + frontend + ads + caddy + the whole monitoring stack).
    flavor_name    = "s-general-1x2" # 1 vCPU / 2 GB — for customer360-api
    root_disk_size = 20
  }
  "tracking" = {
    # data-tracking-api (FastAPI event ingestion on :8010 -> S3/vStorage NDJSON, Redis session
    # cache + rate limit). Its OWN dedicated box (not co-located on the shared api box) so a
    # beacon-traffic spike can't starve api/keycloak/redis. Deployed by ../server/deploy-tracking.sh.
    flavor_name    = "s-general-1x2" # 1 vCPU / 2 GB
    root_disk_size = 20
    name           = "tracking" # -> c360-api-uat-tracking
  }
}

# All resolved from discover-catalog.py for THIS account's live AZ (HCM03-1C):
# - HCM3 only offers the s-general-* family; s2-general-* is a different (sold-out) AZ.
# - flavor_zone_id is set directly to the HCM03-1C "General Purpose Code S" zone: its
#   display name collides across AZs, so the name lookup would hit the sold-out 1A zone.
# - image_id direct: the OS images aren't associated with this flavor zone, so the
#   image_name lookup would fail — bypass it. (s-general-1x2 metaData supports Ubuntu.)
# - The default volume-type lookup returns the HCM03-1A SSD/NVME zones, which are
#   DISABLED ("contact to enable") — so 1C rejects them. The real HCM03-1C SSD zone
#   (C0A35725-…, enabled) is only returned via the ?zoneId= query the data source can't
#   send, so we pin root_disk_type_id directly to its "3000" tier.
flavor_zone_id        = "9818AAB0-8DC5-4FED-898B-9EFD804AB137"       # "General Purpose Code S" @ HCM03-1C (not sold out)
flavor_zone_name      = "General Purpose Code S"                     # unused while flavor_zone_id is set (kept for reference)
volume_type_zone_name = "SSD"                                        # unused while root_disk_type_id is set (kept for reference)
root_disk_type_name   = "3000"                                       # unused while root_disk_type_id is set
root_disk_type_id     = "vtype-e782f8e1-0569-11f0-a0a4-ec2a72332f83" # SSD "3000" @ HCM03-1C (enabled)
image_name            = "1_Ubuntu-24.04x64"                          # unused while image_id is set (kept for reference)
image_id              = "img-54743c32-3cab-4566-9b5b-b21452300d97"   # 1_Ubuntu-24.04x64 (non-UEFI, fits gen-1 s-general)

# Place this box in the SAME subnet as the DB so it can reach the DB's PRIVATE ip.
# Do NOT create a fresh isolated VPC here — it couldn't route to the DB. Fill these from
# the postgres UAT outputs:
#   cd ../../postgres && terraform workspace select uat && terraform output network_id subnet_id
create_network = false
project_id     = "pro-8986f5c6-02ca-4647-be9a-4070bb100559"
network_id     = "net-d25c55be-b404-440b-8e32-f4064ff35d0d" # postgres UAT VPC (c360-vpc-uat, HCM03-1C)
subnet_id      = "sub-7c1f6eff-7244-4a29-a3cf-3592745ea0e7" # postgres UAT subnet (c360-subnet-uat, HCM03-1C)

zone_id           = "HCM03-1C"
encryption_volume = false

# Public IP + how you SSH in from your laptop (RSA private key from `ssh-keygen`):
#   ssh -i ~/.ssh/c360-api_rsa leocdp360@<floating_ip>
attach_floating = true

# security_group is REQUIRED by the provider. Only the project "Default" secgroup exists,
# and it opens nothing inbound — open_ssh adds an inbound tcp/22 rule to it (below).
security_group = ["secg-7c1e85ec-8028-460a-8592-99463f198831"] # "Default"

# Open inbound SSH (tcp/22) on the Default secgroup. TIGHTEN ssh_ingress_cidr to your IP.
open_ssh         = true
ssh_ingress_cidr = "0.0.0.0/0" # <-- change to "<your-public-ip>/32"

# Intra-VPC ops ports on the shared Default secgroup (it opens nothing inbound by default).
# The tracking box (server key "tracking", 10.100.1.8) is not co-located on the api box, so its
# cross-box hops are opened explicitly (co-located services reach each other on 127.0.0.1). The
# tracking-api only needs: Caddy -> its app port, and it -> the api-box Redis (rate-limit + session
# cache; Redis is optional/fail-open). VERIFY IPs with `terraform output servers`; apply out-of-band
# with `./deploy.sh uat apply` (CD never runs infra Terraform).
#   * 9001 -> Portainer agent on the tracking box, reached by the Portainer box (api 10.100.1.5).
#   * 8010 -> data-tracking-api on the tracking box, reached by Caddy on the api box (10.100.1.5) for /data.
#   * 6580 -> the api-box Redis (10.100.1.5), reached by the tracking box (10.100.1.8) for its cache.
#   * 4318 -> the api-box Jaeger OTLP/HTTP (10.100.1.5), reached by the tracking box for request traces.
extra_ingress = [
  { port = 9001, cidr = "10.100.1.5/32" }, # Portainer agent   <- api box (Portainer)
  { port = 8010, cidr = "10.100.1.5/32" }, # data-tracking-api <- api box (Caddy /data)
  { port = 6580, cidr = "10.100.1.8/32" }, # api-box Redis      <- tracking box (rate-limit + session cache)
  { port = 4318, cidr = "10.100.1.8/32" }, # api-box Jaeger OTLP <- tracking box (request traces)
]

# LOGIN via cloud-init user_data. The VNG Ubuntu 24.04 image's ssh-keygen.service FAILS at
# boot (persistently) -> no SSH host keys -> sshd exits -> port 22 REFUSED. cloud-init's
# config stage regenerates host keys later, but nothing restarts sshd; so our runcmd runs
# `ssh-keygen -A` + `systemctl restart ssh` to bring sshd up. It also creates the login
# user (leocdp360 + the RSA key) and installs psql. Native ssh_key/user args are OFF
# (VNG forbids user_data alongside them).
create_ssh_key = false
ssh_key_name   = ""
user_name      = ""
user_password  = ""

user_data = <<EOT
#cloud-config
users:
  - default
  - name: leocdp360
    groups: [sudo]
    shell: /bin/bash
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    ssh_authorized_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKpvwH+KHdW9w9GLatiEzNjzsEPB95VDrWXp+rEnpI5k c360-api
      - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC6AN5tYmcvGXOdbRZSDYMg1fvIw38w07CN4BIWQ/1T7SabZS5X3g1EdLS5AEqpq7mjP6Bo6DDOW7JpamWa6n5n9RW875H7thCkYZ39fsiZJ68eTpc95b2tIjDhRiD7pul2LtUDXwE5bfxxMNS9Gr6aos+lGZlOUjlEqJ5ybUhqbMjZCmhigyGbL7tPG3ltkwV6asOB2MGwGrvF1ulgdTmpEt8HONaw5aYD4ISpu9v7+wHOt9cuSt6fOgI4WAFsCy7086wgjWIyq+zwzCIctobD4So5nJ+zgMa+2YU7psbgQt1hSwHGZRmQHt133s/L9EKQQ25Fi8zbE2gSwnue3M0ARmTGMUgGKb32vyzcqu3Nt1plMavzlRgcKxO8wXJzlKSc1B//bnE2G1+e57tuxBPDxelXjaIbx0v0chEtyOyPCGwd/1Btk0Gg3kSBvXwFE3MlhIYCg3irrgJCLwoXwyYnyEx4WRnF4nffEX14fhOmZ0nD3Kf8pFVCcKcFCV2/jlCcL0VLBA+O6LFxYw2zCQQ49yV827Nn0oMimR0zyprNSeVCvn+4dyIf1w8yiY9U5DS+yWoaCSTikf35cCqKhTcniWorg7nJLDbHkmdpmE9Jg+7enwXmFvfPaPTPy1xn3GFLDPnw5qG32xwkFmj+0jX/RIE2yE00N93fLzD3rJW0dw== c360-api
packages:
  - postgresql-client
# VNG Ubuntu image fixes on first boot: (1) ssh-keygen.service fails (read-only fs) -> run
# ssh-keygen -A; (2) sshd ships Port 234 -> force 22; (3) sshd_config.d drop-ins are NOT
# Included, so APPEND auth settings to the MAIN config; (4) the hardened sshd rejects RSA,
# so allow rsa-sha2 + ed25519. Then drop socket-activation and (re)start the daemon.
runcmd:
  - ssh-keygen -A
  - sed -i 's/^Port 234$/Port 22/' /etc/ssh/sshd_config
  - printf '\nPubkeyAuthentication yes\nPubkeyAcceptedAlgorithms +ssh-rsa,rsa-sha2-256,rsa-sha2-512,ssh-ed25519\n' >> /etc/ssh/sshd_config
  - [ bash, -c, "echo leocdp360:$(openssl rand -base64 18) | chpasswd; usermod -U leocdp360 2>/dev/null || true; chmod 755 /home/leocdp360; chmod 700 /home/leocdp360/.ssh; chmod 600 /home/leocdp360/.ssh/authorized_keys; chown -R leocdp360:leocdp360 /home/leocdp360/.ssh" ]
  - systemctl disable --now ssh.socket
  - systemctl enable ssh.service
  - systemctl restart ssh.service
EOT
