from redis import Redis

from app.config import settings

# Atomic token-bucket check-and-consume. Runs as a single Redis EVAL so a
# concurrent listener process can't read a stale token count between the
# GET and the SET (a plain GET/compute/SET round trip would race).
#
# KEYS[1] = bucket key
# ARGV[1] = capacity (max tokens)
# ARGV[2] = refill_rate (tokens per second = capacity / window_seconds)
# ARGV[3] = now (unix timestamp, float)
_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local bucket = redis.call("HMGET", key, "tokens", "last_refill")
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call("HMSET", key, "tokens", tostring(tokens), "last_refill", tostring(now))
redis.call("EXPIRE", key, math.ceil(capacity / refill_rate) * 2)

return allowed
"""


def allow(redis_client: Redis, recipient: str, now: float) -> bool:
    """Token-bucket check for one recipient. Returns True if this
    notification may proceed, False if the recipient is currently
    rate-limited. Consumes a token on allow.
    """
    capacity = settings.rate_limit_max_tokens
    refill_rate = capacity / settings.rate_limit_window_seconds

    result = redis_client.eval(
        _TOKEN_BUCKET_SCRIPT,
        1,
        f"rate_limit:{recipient}",
        capacity,
        refill_rate,
        now,
    )
    return bool(result)
