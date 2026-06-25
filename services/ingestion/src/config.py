import json
import os
from dotenv import load_dotenv

load_dotenv()

DEFISTREAM_API_KEY = os.environ.get("DEFISTREAM_API_KEY", "")

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "tradernick")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "tradernick")
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DB", "tradernick")

def _parse_token_csv(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


# ── Token batches ─────────────────────────────────────────────────────────
# Tokens are ingested in *batches*. Batch 1 is the original INGEST_TOKENS
# roster; further batches are supplied via INGEST_TOKENS_BATCH_2,
# INGEST_TOKENS_BATCH_3, … (scanned in order until the first missing var).
#
# Batches exist ONLY so backfill jobs can target a subset (so adding new
# tokens doesn't force a full-history re-backfill of everything). The live
# streams and gap detection always poll the FLAT UNION of every batch
# (`INGEST_TOKENS`, below) — i.e. live always covers all batches. The
# trading dashboard is unaware of batches; it's purely an ingestion concern.
# Adding a later batch is a pure env change — no code edit needed.
_INGEST_BATCH_1 = _parse_token_csv(os.environ.get(
    "INGEST_TOKENS",
    "BTC,ETH,SOL,ARB,"
    "LTC,TRX,AAVE,AERO,CAKE,COW,ENA,ETHFI,FET,FIL,HYPE,"
    "MORPHO,PENDLE,RENDER,SUSHI,UNI,WLD,VIRTUAL,PAXG,ZEC,"
    "TON,NEAR,DOGE,TAO"
))

INGEST_TOKEN_BATCHES: list[tuple[str, list[str]]] = []
if _INGEST_BATCH_1:
    INGEST_TOKEN_BATCHES.append(("Batch 1", _INGEST_BATCH_1))
_batch_n = 2
while True:
    _raw = os.environ.get(f"INGEST_TOKENS_BATCH_{_batch_n}")
    if _raw is None:
        break
    _toks = _parse_token_csv(_raw)
    if _toks:
        INGEST_TOKEN_BATCHES.append((f"Batch {_batch_n}", _toks))
    _batch_n += 1

# Named batches — supplied as a JSON map {"<name>": "<CSV>", ...} via
# INGEST_NAMED_BATCHES. Unlike the numbered batches above these carry a
# human-readable category name (e.g. "Majors", "Memes"). They're appended to
# INGEST_TOKEN_BATCHES so they participate in the flat union and act as the
# SEED for the runtime token-batch store (token_batches.py). After first run
# the admin panel is the source of truth; this env var only re-seeds an empty
# store. Malformed JSON is ignored so a bad var can't break startup.
_named_raw = os.environ.get("INGEST_NAMED_BATCHES")
if _named_raw:
    try:
        for _name, _csv in json.loads(_named_raw).items():
            _toks = _parse_token_csv(_csv)
            if _toks:
                INGEST_TOKEN_BATCHES.append((str(_name), _toks))
    except (ValueError, TypeError, AttributeError):
        pass

# Flat, de-duplicated union across all batches (first occurrence wins, order
# preserved). Drop-in replacement for the old flat list — every existing
# consumer (live streams, gap detection, backfill default roster) keeps
# working and automatically covers every batch.
INGEST_TOKENS: list[str] = []
_seen_tokens: set[str] = set()
for _bname, _btoks in INGEST_TOKEN_BATCHES:
    for _bt in _btoks:
        if _bt not in _seen_tokens:
            _seen_tokens.add(_bt)
            INGEST_TOKENS.append(_bt)

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
# Collapse PAIRS into per-chain token lists. The live job fires ONE
# multi-token request per chain (with `.ignore_non_existing()`) so
# DeFiStream skips tokens that aren't deployed on that chain. 5 chains
# → 5 calls/tick regardless of token-roster size.
EVM_ERC20_BY_CHAIN: dict[str, list[str]] = {}
for _ch, _tok in EVM_ERC20_PAIRS:
    EVM_ERC20_BY_CHAIN.setdefault(_ch, []).append(_tok)
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

# --- Lido liquid-staking events -------------------------------------------
# Mainnet events live only on ETH (stETH ⇆ ETH). L2 events live on the
# bridge-deployed wstETH chains DeFiStream covers (currently ARB, BASE,
# OP, ZK, MANTLE, MODE, SONEIUM, UNI, ZIRCUIT — 9 chains). The L2 chain
# list is parametrised so a new bridge deployment is a pure-env change.
LIDO_EVENTS_ENABLED = os.environ.get("LIDO_EVENTS_ENABLED", "1") == "1"
LIDO_ETH_EVENTS = _parse_csv_list_raw(
    os.environ.get("LIDO_ETH_EVENTS", "deposit,withdrawal_request,withdrawal_claimed")
)
LIDO_L2_EVENTS = _parse_csv_list_raw(
    os.environ.get("LIDO_L2_EVENTS", "l2_deposit,l2_withdrawal_request")
)
# DeFiStream's catalogue lists 9 L2s for Lido coverage, but the runtime
# API only has ARB + BASE configured today — the others return
# "Network X not configured". We keep just the live two in the default so
# the poller doesn't burn rate budget on dead chains every minute. Add
# more to the env var as DeFiStream lights them up; the backfill driver
# already fast-forwards any "not configured" chain on its own.
LIDO_L2_CHAINS = _parse_csv_list(
    os.environ.get("LIDO_L2_CHAINS", "ARB,BASE")
)


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
    # The deepest pools across the 4 EVMs — covers >80% of total volume.
    # BSC pools removed: DeFiStream reports "Uniswap pool not found" for
    # BSC:BNB/USDT/500 (PancakeSwap is the dominant V3-style DEX on BSC,
    # not Uniswap). Use the PancakeSwap-specific endpoint if/when added.
    "ETH:USDC/WETH/500,USDC/WETH/3000,USDT/WETH/500,USDC/WBTC/3000,WBTC/WETH/3000"
    ";"
    "ARB:USDC/WETH/500,ARB/USDC/500"
    ";"
    "BASE:USDC/WETH/500,CBBTC/WETH/3000"
)
UNI_V3_LIVE_POOLS = _parse_uniswap_pools(
    os.environ.get("UNI_V3_LIVE_POOLS", UNISWAP_V3_LIVE_POOLS_DEFAULT)
)

# --- AAVE v2 events --------------------------------------------------------
# V2 is wound down on most networks — only ETH + POLYGON have any meaningful
# remaining volume. AVAX is listed by DeFiStream's catalogue but the runtime
# rejects with "not configured" so we leave it off the live default.
AAVE_V2_EVENTS_ENABLED = os.environ.get("AAVE_V2_EVENTS_ENABLED", "1") == "1"
AAVE_V2_CHAINS = _parse_csv_list(os.environ.get("AAVE_V2_CHAINS", "ETH,POLYGON"))

# --- AAVE v4 events --------------------------------------------------------
# V4 launched on ETH-only as of late 2025/early 2026. 5 events (no flashloan).
AAVE_V4_EVENTS_ENABLED = os.environ.get("AAVE_V4_EVENTS_ENABLED", "1") == "1"
AAVE_V4_CHAINS = _parse_csv_list(os.environ.get("AAVE_V4_CHAINS", "ETH"))

# --- Morpho events (ETH + BASE) -------------------------------------------
MORPHO_EVENTS_ENABLED = os.environ.get("MORPHO_EVENTS_ENABLED", "1") == "1"
MORPHO_CHAINS = _parse_csv_list(os.environ.get("MORPHO_CHAINS", "ETH,BASE"))

# --- Spark events (ETH only) ----------------------------------------------
SPARK_EVENTS_ENABLED = os.environ.get("SPARK_EVENTS_ENABLED", "1") == "1"
SPARK_CHAINS = _parse_csv_list(os.environ.get("SPARK_CHAINS", "ETH"))


# --- GMX V2 events (perp DEX, ARB-only in defistream 2.14) ----------------
# GMX V2 returns ALL markets in a single network() call — no per-market
# filter on the builder. So live polling is just 9 calls/tick (one per
# event on ARB).  Per-market filtering happens at chart time via the
# market_name column.
GMX_EVENTS_ENABLED = os.environ.get("GMX_EVENTS_ENABLED", "1") == "1"
GMX_CHAINS = _parse_csv_list(os.environ.get("GMX_CHAINS", "ARB"))


# --- Uniswap V2 pools ------------------------------------------------------
# V2 has no fee tier (fixed 0.30%), so the pool grammar is
# `<chain>:<sym0/sym1>,<sym0/sym1>;<chain2>:...`. _parse_uniswap_pools is
# fee-tier-aware so we reuse it with a sentinel fee=0 (which the V2 callers
# strip away before issuing requests).
def _parse_uniswap_v2_pools(raw: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group or ":" not in group:
            continue
        chain, pools_str = group.split(":", 1)
        chain = chain.strip().upper()
        for pool in pools_str.split(","):
            pool = pool.strip()
            parts = pool.split("/")
            if len(parts) != 2:
                continue
            sym_a, sym_b = parts[0].strip(), parts[1].strip()
            if not sym_a or not sym_b:
                continue
            symbol0, symbol1 = sorted([sym_a.upper(), sym_b.upper()])
            out.append((chain, symbol0, symbol1))
    return out


UNISWAP_V2_POOLS_DEFAULT = (
    # ETH — V2 still sees real volume on the canonical USDC/WETH + WBTC pairs
    "ETH:USDC/WETH,USDT/WETH,DAI/WETH,WBTC/WETH,DAI/USDC"
    ";"
    # ARB / BASE / BSC / POLYGON have Uniswap V2 deployments (2023 redeploys)
    # but volume is thin — keep a small set for forward compatibility.
    "ARB:USDC/WETH,USDT/WETH"
    ";"
    "BASE:USDC/WETH,CBBTC/WETH"
    ";"
    "BSC:USDC/WETH,USDT/WETH"
    ";"
    "POLYGON:USDC/WETH"
)
UNI_V2_POOLS = _parse_uniswap_v2_pools(
    os.environ.get("UNI_V2_POOLS", UNISWAP_V2_POOLS_DEFAULT)
)
UNI_V2_ENABLED = os.environ.get("UNI_V2_ENABLED", "1") == "1"
# Trimmed live-poll subset — same shared rate-budget reasoning as V3.
UNISWAP_V2_LIVE_POOLS_DEFAULT = "ETH:USDC/WETH,USDT/WETH,DAI/USDC"
UNI_V2_LIVE_POOLS = _parse_uniswap_v2_pools(
    os.environ.get("UNI_V2_LIVE_POOLS", UNISWAP_V2_LIVE_POOLS_DEFAULT)
)


# --- Uniswap V4 pools ------------------------------------------------------
# V4 pool identity: (chain, sym0, sym1, fee, tick_spacing, hooks). Grammar:
#   `<chain>:<sym0/sym1/fee/tick_spacing>[/<hooks>],...;<chain2>:...`
# hooks defaults to the zero address when omitted. Pairs auto-canonicalise
# (alphabetic order).
def _parse_uniswap_v4_pools(raw: str) -> list[tuple[str, str, str, int, int, str]]:
    out: list[tuple[str, str, str, int, int, str]] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group or ":" not in group:
            continue
        chain, pools_str = group.split(":", 1)
        chain = chain.strip().upper()
        for pool in pools_str.split(","):
            pool = pool.strip()
            parts = pool.split("/")
            if len(parts) < 4:
                continue
            sym_a, sym_b, fee_s, ts_s = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            hooks = parts[4].strip() if len(parts) >= 5 else "0x0000000000000000000000000000000000000000"
            if not sym_a or not sym_b:
                continue
            try:
                fee = int(fee_s); ts = int(ts_s)
            except ValueError:
                continue
            symbol0, symbol1 = sorted([sym_a.upper(), sym_b.upper()])
            out.append((chain, symbol0, symbol1, fee, ts, hooks))
    return out


UNISWAP_V4_POOLS_DEFAULT = ""
# V4 pool list is deliberately empty by default — the canonical V3 pool
# names don't map cleanly to V4 (V4 needs fee + tick_spacing + hooks per
# pool, often differing per real-world deployment). The live group +
# backfill driver both idle when this list is empty, so the protocol
# stays dormant until pools are supplied via the UNI_V4_POOLS env var
# or the /jobs/backfill/uniswap_v4_events endpoint's `pools` arg.
UNI_V4_POOLS = _parse_uniswap_v4_pools(
    os.environ.get("UNI_V4_POOLS", UNISWAP_V4_POOLS_DEFAULT)
)
UNI_V4_ENABLED = os.environ.get("UNI_V4_ENABLED", "1") == "1"
UNI_V4_LIVE_POOLS_DEFAULT = ""
UNI_V4_LIVE_POOLS = _parse_uniswap_v4_pools(
    os.environ.get("UNI_V4_LIVE_POOLS", UNI_V4_LIVE_POOLS_DEFAULT)
)


# --- Aerodrome (BASE-only, concentrated pools) -----------------------------
# Grammar: `BASE:<sym0/sym1/tick_spacing>,...` (single chain so no `;`).
def _parse_aero_cl_pools(raw: str) -> list[tuple[str, str, str, int]]:
    out: list[tuple[str, str, str, int]] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group or ":" not in group:
            continue
        chain, pools_str = group.split(":", 1)
        chain = chain.strip().upper()
        for pool in pools_str.split(","):
            parts = pool.strip().split("/")
            if len(parts) != 3:
                continue
            try:
                ts = int(parts[2].strip())
            except ValueError:
                continue
            symbol0, symbol1 = sorted([parts[0].strip().upper(), parts[1].strip().upper()])
            if not symbol0 or not symbol1:
                continue
            out.append((chain, symbol0, symbol1, ts))
    return out


AERO_CL_POOLS_DEFAULT = (
    # Top Aero concentrated pools by volume — all on BASE.
    "BASE:USDC/WETH/100,USDC/WETH/1,CBBTC/USDC/100,CBBTC/WETH/200,AERO/USDC/200,AERO/WETH/200"
)
AERO_POOLS = _parse_aero_cl_pools(os.environ.get("AERO_POOLS", AERO_CL_POOLS_DEFAULT))
AERO_ENABLED = os.environ.get("AERO_ENABLED", "1") == "1"
AERO_LIVE_POOLS_DEFAULT = "BASE:USDC/WETH/100"
AERO_LIVE_POOLS = _parse_aero_cl_pools(
    os.environ.get("AERO_LIVE_POOLS", AERO_LIVE_POOLS_DEFAULT)
)


# --- Aerodrome basic pools (Solidly-style v1, BASE only) -------------------
# Grammar: `BASE:<sym0/sym1/stable>,...` where stable is 'v' (vAMM, false)
# or 's' (sAMM, true). Per-pair stable flag is part of the pool identity.
def _parse_aero_basic_pools(raw: str) -> list[tuple[str, str, str, bool]]:
    out: list[tuple[str, str, str, bool]] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group or ":" not in group:
            continue
        chain, pools_str = group.split(":", 1)
        chain = chain.strip().upper()
        for pool in pools_str.split(","):
            parts = pool.strip().split("/")
            if len(parts) != 3:
                continue
            sym_a, sym_b, st_s = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()
            if not sym_a or not sym_b or st_s not in ("v", "s"):
                continue
            symbol0, symbol1 = sorted([sym_a.upper(), sym_b.upper()])
            out.append((chain, symbol0, symbol1, st_s == "s"))
    return out


AERO_BASIC_POOLS_DEFAULT = (
    # Top basic-pool vAMMs by volume — all on BASE.
    "BASE:USDC/WETH/v,USDC/AERO/v,WETH/AERO/v,USDC/CBBTC/v"
)
AERO_BASIC_POOLS = _parse_aero_basic_pools(
    os.environ.get("AERO_BASIC_POOLS", AERO_BASIC_POOLS_DEFAULT)
)
AERO_BASIC_ENABLED = os.environ.get("AERO_BASIC_ENABLED", "1") == "1"
AERO_BASIC_LIVE_POOLS_DEFAULT = "BASE:USDC/WETH/v"
AERO_BASIC_LIVE_POOLS = _parse_aero_basic_pools(
    os.environ.get("AERO_BASIC_LIVE_POOLS", AERO_BASIC_LIVE_POOLS_DEFAULT)
)

# Pace every DeFiStream HTTP call through a per-process token bucket +
# concurrency semaphore. Imported here so any group/backfill that does
# `import config` (which is all of them) picks up the patch before its
# first AsyncDeFiStream() instantiation.
from ds_throttle import install as _install_ds_throttle
_install_ds_throttle()
