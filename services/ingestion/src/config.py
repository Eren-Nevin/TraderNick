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


def _parse_chain_token_pairs(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group:
            continue
        if ":" not in group:
            continue
        chain, tokens = group.split(":", 1)
        chain = chain.strip().upper()
        for tok in tokens.split(","):
            tok = tok.strip().upper()
            if chain and tok:
                pairs.append((chain, tok))
    return pairs


def _parse_csv_list(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _parse_csv_list_raw(raw: str) -> list[str]:
    """Like _parse_csv_list but preserves casing — used where the API
    expects exact tokens (e.g. AAVE eth_market_type accepts 'Core' /
    'Prime' / 'EtherFi' but rejects the uppercased forms)."""
    return [s.strip() for s in raw.split(",") if s.strip()]


EVM_ERC20_PAIRS = _parse_chain_token_pairs(
    os.environ.get(
        "EVM_ERC20_TRANSFERS",
        "ETH:USDT,USDC,DAI,LINK;ARB:USDT,USDC,DAI,LINK;POLYGON:USDT,USDC,DAI,LINK;BASE:USDT,USDC,DAI,LINK;BSC:USDT,USDC,DAI,LINK",
    )
)
EVM_NATIVE_CHAINS = _parse_csv_list(os.environ.get("EVM_NATIVE_TRANSFERS", ""))
BTC_TRANSFERS_ENABLED = os.environ.get("BTC_TRANSFERS_ENABLED", "1") == "1"
TRON_NATIVE_TRANSFERS_ENABLED = os.environ.get("TRON_NATIVE_TRANSFERS_ENABLED", "1") == "1"
TRON_TRC20_TOKENS = _parse_csv_list(os.environ.get("TRON_TRC20_TRANSFERS", "USDT"))

# AAVE v3 — chains + ETH-only market types. Live polling iterates the
# cross product (chain, eth_market) × (deposit/withdraw/borrow/repay/
# flashloan/liquidation). eth_market is honoured only on ETH; on other
# chains we issue a single call per event.
AAVE_EVENTS_CHAINS = _parse_csv_list(os.environ.get("AAVE_EVENTS_CHAINS", ""))
AAVE_ETH_MARKETS = _parse_csv_list_raw(os.environ.get("AAVE_ETH_MARKETS", "Core,Prime,EtherFi"))
AAVE_EVENTS_ENABLED = os.environ.get("AAVE_EVENTS_ENABLED", "1") == "1"
