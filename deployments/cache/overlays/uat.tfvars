# UAT overlay — Redis runs as a DOCKER CONTAINER on the api server VM.
# Apply with:  ./deploy.sh uat        (SSHes to the api box; does NOT run Terraform)
#
# Only redis_port / redis_image / api_server_key are consumed (by deploy.sh).
# deploy_managed=false means "no managed MemStore here" — belt-and-suspenders in
# case someone runs `terraform apply` against this overlay (it becomes a no-op).

deploy_managed = false

redis_port          = 6580                      # redis.conf's port (also the api's REDIS_PORT)
redis_image         = "customer360-redis:local" # built from the repo ./redis (redis:8-alpine + cache-tuned conf)
redis_build_context = "../../redis"             # repo redis/ dir (relative to this deployment)
api_server_key      = "api"                     # ../server for_each key: c360-api-uat-api (private 10.100.1.5)
