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


# Uniswap V3 — each pool is (chain, symbol0, symbol1, fee_tier). Pools are
# semicolon-separated groups, each group is `<chain>:<pool>,<pool>...` where
# a pool is `<symbol0>/<symbol1>/<fee>`. Symbols should be canonical (address-
# alphabetic) order, e.g. USDC/WETH/500 not WETH/USDC/500 — the live group
# normalises automatically by sorting the pair, so either form parses, but
# the canonical form is what DeFiStream returns in `token0` / `token1`.
def _parse_uniswap_pools(raw: str) -> list[tuple[str, str, str, int]]:
    out: list[tuple[str, str, str, int]] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group or ":" not in group:
            continue
        chain, pools_str = group.split(":", 1)
        chain = chain.strip().upper()
        for pool in pools_str.split(","):
            pool = pool.strip()
            parts = pool.split("/")
            if len(parts) != 3:
                continue
            sym_a, sym_b, fee_s = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if not sym_a or not sym_b:
                continue
            try:
                fee = int(fee_s)
            except ValueError:
                continue
            # canonicalise the pair (alphabetic order matches DeFiStream's
            # address-ordered convention for these tokens)
            symbol0, symbol1 = sorted([sym_a.upper(), sym_b.upper()])
            out.append((chain, symbol0, symbol1, fee))
    return out


UNISWAP_V3_POOLS_DEFAULT = (
    # ETH — heaviest pools
    "ETH:USDC/WETH/500,USDC/WETH/3000,USDT/WETH/500,USDT/WETH/3000,"
    "USDC/WBTC/3000,WBTC/WETH/3000,DAI/USDC/100,DAI/WETH/3000,"
    "USDC/USDT/100,LINK/WETH/3000"
    ";"
    # ARB
    "ARB:USDC/WETH/500,USDC/WETH/3000,USDT/WETH/500,"
    "ARB/USDC/500,USDC/WBTC/3000,WBTC/WETH/3000,DAI/USDC/100,LINK/WETH/3000"
    ";"
    # BASE
    "BASE:USDC/WETH/500,USDC/WETH/3000,CBBTC/USDC/3000,CBBTC/WETH/3000,"
    "DAI/USDC/100,USDC/USDT/100,LINK/WETH/3000"
    ";"
    # BSC
    "BSC:USDC/WETH/500,USDC/WETH/3000,USDT/WETH/500,"
    "BNB/USDT/500,BNB/USDC/500,USDC/USDT/100,LINK/WETH/3000"
    ";"
    # POLYGON
    "POLYGON:USDC/WETH/500,USDC/WETH/3000,USDC/USDT/100,"
    "POL/USDC/500,USDC/WBTC/3000,WBTC/WETH/3000,LINK/WETH/3000"
)

UNI_V3_POOLS = _parse_uniswap_pools(os.environ.get("UNI_V3_POOLS", UNISWAP_V3_POOLS_DEFAULT))
UNI_V3_ENABLED = os.environ.get("UNI_V3_ENABLED", "1") == "1"

# Pools polled at 60s tick. DeFiStream's rate budget (≈50 req/min, shared
# across the AAVE group and any backfill running) means we can't realistically
# live-fan-out across the full ~40-pool catalogue (4 events each = 160 calls /
# minute). UNI_V3_LIVE_POOLS narrows live polling to a tight set of high-volume
# pools — backfill still uses the full UNI_V3_POOLS list, so historic coverage
# is unaffected.
UNISWAP_V3_LIVE_POOLS_DEFAULT = (
    # The 10 deepest pools across the 5 EVMs — covers >80% of total volume.
    "ETH:USDC/WETH/500,USDC/WETH/3000,USDT/WETH/500,USDC/WBTC/3000,WBTC/WETH/3000"
    ";"
    "ARB:USDC/WETH/500,ARB/USDC/500"
    ";"
    "BASE:USDC/WETH/500,CBBTC/WETH/3000"
    ";"
    "BSC:BNB/USDT/500"
)
UNI_V3_LIVE_POOLS = _parse_uniswap_pools(
    os.environ.get("UNI_V3_LIVE_POOLS", UNISWAP_V3_LIVE_POOLS_DEFAULT)
)
