# Ad Request Cache Utilities to support the Ad Server API.
# TODO 
# 1. Add a cache for the ad request payloads (placement_id, tenant_id, etc.) to avoid repeated DB queries for the same request.
# 2. Add a cache for the ad selection results (list of ads) to avoid repeated DB queries for the same request.
# 3. create a RedisRepository class to handle caching in Redis, and migrate the existing caching logic to use this new class.
# 4. create a CacheKeyGenerator class to generate unique cache keys for ad requests and ad selection results, based on the request parameters.
# 5. create a CacheManager class to manage the caching logic, including cache expiration and invalidation.
# 6. create a CacheConfig class to hold the cache configuration settings, such as cache TTL and cache size limits.
# all CacheConfig settings should be loaded from the AdServerSettings class in core/config.py, and should be configurable via environment variables or .env file.
# update ads-server/.env.example for the new cache configuration settings, and provide documentation for each setting.