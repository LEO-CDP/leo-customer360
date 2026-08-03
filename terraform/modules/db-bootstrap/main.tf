terraform {
  required_providers {
    null = {
      source = "hashicorp/null"
    }
  }
}

# In-database bootstrap that the vDB provider cannot do itself:
#   1. enable required extensions (postgis, vector, pg_trgm, fuzzystrmatch, ...)
#   2. apply the schema (database-init/database-schema.sql)
#   3. create the non-superuser RLS role customer360_app  <-- critical: RLS is
#      inert when the app connects as the superuser
#   4. seed core data (database-init/init-core-database.sql)
#   5. optional extras (data-view-for-llm.sql)
#
# Requires `psql` and `bash` on the machine running Terraform, and network
# reachability to the DB (set public_access = true on the instance, or run this
# from inside the VPC / as a K8s Job). Disabled by default (enabled = false):
# the recommended production path is a dedicated migration job in CI/CD or a
# Kubernetes Job that runs these same idempotent scripts.

locals {
  app_role_sql = templatefile("${path.module}/templates/create-app-role.sql.tftpl", {
    app_role     = var.app_role_name
    app_password = var.app_role_password
    schema       = var.db_schema
  })
}

resource "null_resource" "bootstrap" {
  count = var.enabled ? 1 : 0

  # Re-run when the instance, any SQL file, the role definition, or the target
  # connection changes. All applied SQL is idempotent, so re-runs are safe.
  triggers = {
    instance_id = var.instance_id
    connection  = "${var.host}:${var.port}/${var.db_name}"
    ext_sha     = filesha1(var.extensions_sql)
    schema_sha  = filesha1(var.schema_sql)
    seed_sha    = filesha1(var.seed_sql)
    extra_sha   = sha1(join(",", [for f in var.extra_sql : filesha1(f)]))
    role_sha    = sha1(local.app_role_sql)
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      PGPASSWORD = var.master_password
    }
    command = templatefile("${path.module}/templates/run.sh.tftpl", {
      host           = var.host
      port           = var.port
      master_user    = var.master_user
      db_name        = var.db_name
      app_role       = var.app_role_name
      app_role_sql   = local.app_role_sql
      extensions_sql = abspath(var.extensions_sql)
      schema_sql     = abspath(var.schema_sql)
      seed_sql       = abspath(var.seed_sql)
      extra_sql      = [for f in var.extra_sql : abspath(f)]
    })
  }
}
