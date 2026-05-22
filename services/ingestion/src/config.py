import os
from dotenv import load_dotenv

load_dotenv()

DEFISTREAM_API_KEY = os.environ.get("DEFISTREAM_API_KEY", "")

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "tradernick")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "tradernick")
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DB", "tradernick")

INGEST_TOKENS = [t.strip() for t in os.environ.get("INGEST_TOKENS", "BTC,ETH,SOL,ARB,OP").split(",") if t.strip()]

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change_me")

MAX_CONCURRENT_BACKFILLS = int(os.environ.get("MAX_CONCURRENT_BACKFILLS", "4"))

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
POLL_OVERLAP_MINUTES = int(os.environ.get("POLL_OVERLAP_MINUTES", "3"))
