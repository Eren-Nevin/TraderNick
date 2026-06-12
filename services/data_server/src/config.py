import os
from dotenv import load_dotenv

load_dotenv()

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "tradernick")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "tradernick")
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DB", "tradernick")

# ── Smart-wallet leaderboard cache ───────────────────────────────────
# Read-through cache of resolved per-day wallet sets (see wallets/cache.py).
# Days within SETTLE_DAYS of today are still being ingested, so they're
# resolved live every request and never cached; older days are immutable
# and cached. Entries evict TTL_DAYS after they were last computed — so a
# filter that's edited (→ new content hash) and abandoned ages out on its
# own rather than accumulating, which matters because filters change fast.
SMART_CACHE_ENABLED = os.environ.get("SMART_CACHE_ENABLED", "1") == "1"
SMART_CACHE_SETTLE_DAYS = int(os.environ.get("SMART_CACHE_SETTLE_DAYS", "1"))
SMART_CACHE_TTL_DAYS = int(os.environ.get("SMART_CACHE_TTL_DAYS", "3"))
