output "postgres" {
  value = module.stack.postgres
}

output "redis" {
  value = module.stack.redis
}

output "kafka" {
  value = module.stack.kafka
}

output "vstorage_buckets" {
  value = module.stack.vstorage_buckets
}

output "network" {
  value = module.stack.network
}
