import base64
import hmac
import logging
import os
import tempfile

from sanic import Sanic, response

from scripts.bootstrap_wallets import (
    load_into_clickhouse,
    rematerialize_status,
    rematerialize_transfers,
)

import config
from jobs.manager import (
    JOB_TYPE_BACKFILL_BTC_TRANSFERS,
    JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS,
    JOB_TYPE_BACKFILL_EVM_NATIVE_TRANSFERS,
    JOB_TYPE_BACKFILL_FUNDING_RATE,
    JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS,
    JOB_TYPE_BACKFILL_OHLCV,
    JOB_TYPE_BACKFILL_OPEN_INTEREST,
    JOB_TYPE_BACKFILL_RAW_TRADES,
    JOB_TYPE_BACKFILL_AAVE_EVENTS,
    JOB_TYPE_BACKFILL_UNISWAP_EVENTS,
    JOB_TYPE_BACKFILL_LIDO_EVENTS,
    JOB_TYPE_BACKFILL_AAVE_V2_EVENTS,
    JOB_TYPE_BACKFILL_UNISWAP_V2_EVENTS,
    JOB_TYPE_BACKFILL_UNISWAP_V4_EVENTS,
    JOB_TYPE_BACKFILL_AERO_EVENTS,
    JOB_TYPE_BACKFILL_AERO_BASIC_EVENTS,
    JOB_TYPE_BACKFILL_AAVE_V4_EVENTS,
    JOB_TYPE_BACKFILL_MORPHO_EVENTS,
    JOB_TYPE_BACKFILL_SPARK_EVENTS,
    JOB_TYPE_BACKFILL_TRON_NATIVE_TRANSFERS,
    JOB_TYPE_BACKFILL_TRON_TRC20_TRANSFERS,
    JobManager,
)
from supervisor import Supervisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ingestion] %(levelname)s %(message)s")
log = logging.getLogger("app")

app = Sanic("tradernick_ingestion")
app.config.RESPONSE_TIMEOUT = 60

UNAUTH_PATHS = {"/health"}


def _check_basic_auth(request) -> bool:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    user, pwd = decoded.split(":", 1)
    return (
        hmac.compare_digest(user, config.ADMIN_USER)
        and hmac.compare_digest(pwd, config.ADMIN_PASSWORD)
    )


@app.middleware("request")
async def basic_auth(request):
    if request.path in UNAUTH_PATHS:
        return
    if not _check_basic_auth(request):
        return response.json(
            {"error": "unauthorized"},
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="tradernick"'},
        )


@app.before_server_start
async def startup(app_, _loop):
    app_.ctx.supervisor = Supervisor()
    app_.ctx.supervisor.start([
        "binance_ohlcv",
        "binance_raw_trades",
        "binance_open_interest",
        "binance_long_short_ratios",
        "binance_funding_rate",
        "evm_erc20_transfers",
        "evm_native_transfers",
        "btc_transfers",
        "tron_native_transfers",
        "tron_trc20_transfers",
        "aave_events",
        "uniswap_events",
        "lido_events",
        "aave_v2_events",
        "uniswap_v2_events",
        "uniswap_v4_events",
        "aero_events",
        "aero_basic_events",
        "aave_v4_events",
        "morpho_events",
        "spark_events",
    ])
    app_.ctx.jobs = JobManager()
    try:
        await app_.ctx.jobs.resume_inflight()
    except Exception:
        log.exception("resume_inflight failed (continuing)")


@app.get("/health")
async def health(_request):
    return response.json({"ok": True})


@app.get("/groups")
async def groups(request):
    return response.json(request.app.ctx.supervisor.snapshot())


@app.get("/jobs")
async def list_jobs(request):
    limit = int(request.args.get("limit", "100"))
    return response.json(await request.app.ctx.jobs.list_jobs(limit=limit))


@app.get("/jobs/<job_id>")
async def get_job(request, job_id: str):
    job = await request.app.ctx.jobs.get(job_id)
    if not job:
        return response.json({"error": "not found"}, status=404)
    return response.json(job)


@app.delete("/jobs/<job_id>")
async def cancel_job(request, job_id: str):
    ok = await request.app.ctx.jobs.cancel(job_id)
    if not ok:
        return response.json({"error": "no live subprocess for this job"}, status=409)
    return response.json({"ok": True})


async def _create_backfill(request, job_type: str):
    body = request.json or {}
    tokens = body.get("tokens") or config.INGEST_TOKENS
    days = int(body.get("days", 30))
    force = bool(body.get("force", False))
    if not tokens:
        return response.json({"error": "no tokens"}, status=400)
    if days <= 0 or days > 365:
        return response.json({"error": "days must be in 1..365"}, status=400)
    try:
        job = await request.app.ctx.jobs.create_backfill_args(
            job_type, days, {"tokens": tokens, "force": force}
        )
    except RuntimeError as exc:
        return response.json({"error": str(exc)}, status=429)
    return response.json(job, status=202)


@app.post("/jobs/backfill/binance_ohlcv")
async def backfill_ohlcv(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_OHLCV)


@app.post("/jobs/backfill/binance_raw_trades")
async def backfill_raw_trades(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_RAW_TRADES)


@app.post("/jobs/backfill/binance_open_interest")
async def backfill_open_interest(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_OPEN_INTEREST)


@app.post("/jobs/backfill/binance_long_short_ratios")
async def backfill_long_short_ratios(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS)


@app.post("/jobs/backfill/binance_funding_rate")
async def backfill_funding_rate(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_FUNDING_RATE)


async def _create_transfer_backfill(request, job_type: str, extract_args):
    body = request.json or {}
    days = int(body.get("days", 30))
    force = bool(body.get("force", False))
    if days <= 0 or days > 365:
        return response.json({"error": "days must be in 1..365"}, status=400)
    err, args_extra = extract_args(body)
    if err:
        return response.json({"error": err}, status=400)
    args_extra["force"] = force
    try:
        job = await request.app.ctx.jobs.create_backfill_args(job_type, days, args_extra)
    except RuntimeError as exc:
        return response.json({"error": str(exc)}, status=429)
    return response.json(job, status=202)


def _extract_pairs(body):
    pairs = body.get("pairs")
    if not pairs or not isinstance(pairs, list):
        return "missing pairs (list of [chain, token] tuples)", None
    norm = []
    for p in pairs:
        if not isinstance(p, list) or len(p) != 2:
            return "each pair must be [chain, token]", None
        norm.append([str(p[0]).upper(), str(p[1]).upper()])
    return None, {"pairs": norm}


def _extract_chains(body):
    chains = body.get("chains")
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    return None, {"chains": [str(c).upper() for c in chains]}


def _extract_tokens(body):
    tokens = body.get("tokens")
    if not tokens or not isinstance(tokens, list):
        return "missing tokens (list)", None
    return None, {"tokens": [str(t).upper() for t in tokens]}


def _extract_empty(_body):
    return None, {}


@app.post("/jobs/backfill/evm_erc20_transfers")
async def backfill_evm_erc20_transfers(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS, _extract_pairs)


@app.post("/jobs/backfill/evm_native_transfers")
async def backfill_evm_native_transfers(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_EVM_NATIVE_TRANSFERS, _extract_chains)


@app.post("/jobs/backfill/btc_transfers")
async def backfill_btc_transfers(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_BTC_TRANSFERS, _extract_empty)


@app.post("/jobs/backfill/tron_native_transfers")
async def backfill_tron_native_transfers(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_TRON_NATIVE_TRANSFERS, _extract_empty)


@app.post("/jobs/backfill/tron_trc20_transfers")
async def backfill_tron_trc20_transfers(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_TRON_TRC20_TRANSFERS, _extract_tokens)


_AAVE_VALID_EVENTS = ("deposit", "withdraw", "borrow", "repay", "flashloan", "liquidation")
_AAVE_VALID_MARKETS = ("Core", "Prime", "EtherFi")


def _extract_aave_events(body):
    chains = body.get("chains") or config.AAVE_EVENTS_CHAINS
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    events = body.get("events") or list(_AAVE_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown_events = [e for e in events if e not in _AAVE_VALID_EVENTS]
    if unknown_events:
        return f"unknown events: {unknown_events}", None
    eth_markets = body.get("eth_markets") or list(config.AAVE_ETH_MARKETS)
    if not isinstance(eth_markets, list):
        return "eth_markets must be a list", None
    unknown_markets = [m for m in eth_markets if m not in _AAVE_VALID_MARKETS]
    if unknown_markets:
        return f"unknown eth_markets: {unknown_markets}", None
    return None, {
        "chains": [str(c).upper() for c in chains],
        "events": list(events),
        "eth_markets": list(eth_markets),
    }


@app.post("/jobs/backfill/aave_events")
async def backfill_aave_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AAVE_EVENTS, _extract_aave_events)


_UNI_VALID_EVENTS = ("swap", "deposit", "withdraw", "collect")


def _extract_uniswap_events(body):
    pools = body.get("pools")
    if pools is None:
        pools = [[c, s0, s1, fee] for (c, s0, s1, fee) in config.UNI_V3_POOLS]
    if not pools or not isinstance(pools, list):
        return "missing pools (list of [chain, symbol0, symbol1, fee])", None
    norm: list[list] = []
    for p in pools:
        if not isinstance(p, list) or len(p) != 4:
            return "each pool must be [chain, symbol0, symbol1, fee]", None
        try:
            fee = int(p[3])
        except (TypeError, ValueError):
            return f"fee must be an int, got {p[3]!r}", None
        sym0, sym1 = sorted([str(p[1]).upper(), str(p[2]).upper()])
        norm.append([str(p[0]).upper(), sym0, sym1, fee])
    events = body.get("events") or list(_UNI_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _UNI_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"pools": norm, "events": list(events)}


@app.post("/jobs/backfill/uniswap_events")
async def backfill_uniswap_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_UNISWAP_EVENTS, _extract_uniswap_events)


_LIDO_VALID_EVENTS = (
    "deposit", "withdrawal_request", "withdrawal_claimed",
    "l2_deposit", "l2_withdrawal_request",
)


def _extract_lido_events(body):
    chains = body.get("chains") or (["ETH"] + list(config.LIDO_L2_CHAINS))
    if not isinstance(chains, list):
        return "chains must be a list", None
    events = body.get("events") or list(_LIDO_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _LIDO_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {
        "chains": [str(c).upper() for c in chains],
        "events": list(events),
    }


@app.post("/jobs/backfill/lido_events")
async def backfill_lido_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_LIDO_EVENTS, _extract_lido_events)


_AAVE_V2_VALID_EVENTS = ("deposit", "withdraw", "borrow", "repay", "flashloan", "liquidation")


def _extract_aave_v2_events(body):
    chains = body.get("chains") or list(config.AAVE_V2_CHAINS)
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    events = body.get("events") or list(_AAVE_V2_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _AAVE_V2_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"chains": [str(c).upper() for c in chains], "events": list(events)}


@app.post("/jobs/backfill/aave_v2_events")
async def backfill_aave_v2_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AAVE_V2_EVENTS, _extract_aave_v2_events)


_UNI_V2_VALID_EVENTS = ("swap", "deposit", "withdraw")


def _extract_uniswap_v2_events(body):
    pools = body.get("pools")
    if pools is None:
        pools = [[c, s0, s1] for (c, s0, s1) in config.UNI_V2_POOLS]
    if not pools or not isinstance(pools, list):
        return "missing pools (list of [chain, symbol0, symbol1])", None
    norm: list[list] = []
    for p in pools:
        if not isinstance(p, list) or len(p) != 3:
            return "each pool must be [chain, symbol0, symbol1]", None
        sym0, sym1 = sorted([str(p[1]).upper(), str(p[2]).upper()])
        norm.append([str(p[0]).upper(), sym0, sym1])
    events = body.get("events") or list(_UNI_V2_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _UNI_V2_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"pools": norm, "events": list(events)}


@app.post("/jobs/backfill/uniswap_v2_events")
async def backfill_uniswap_v2_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_UNISWAP_V2_EVENTS, _extract_uniswap_v2_events)


_UNI_V4_VALID_EVENTS = ("swap", "deposit", "withdraw", "initialize")


def _extract_uniswap_v4_events(body):
    pools = body.get("pools")
    if pools is None:
        pools = [[c, s0, s1, fee, ts, hk] for (c, s0, s1, fee, ts, hk) in config.UNI_V4_POOLS]
    if not pools or not isinstance(pools, list):
        return "missing pools (list of [chain, sym0, sym1, fee, tick_spacing, hooks])", None
    norm = []
    for p in pools:
        if not isinstance(p, list) or len(p) < 5:
            return "each pool must be [chain, sym0, sym1, fee, tick_spacing, (hooks)]", None
        try:
            fee = int(p[3]); ts = int(p[4])
        except (TypeError, ValueError):
            return "fee + tick_spacing must be ints", None
        hooks = p[5] if len(p) >= 6 else "0x0000000000000000000000000000000000000000"
        sym0, sym1 = sorted([str(p[1]).upper(), str(p[2]).upper()])
        norm.append([str(p[0]).upper(), sym0, sym1, fee, ts, str(hooks)])
    events = body.get("events") or list(_UNI_V4_VALID_EVENTS)
    if not isinstance(events, list): return "events must be a list", None
    unknown = [e for e in events if e not in _UNI_V4_VALID_EVENTS]
    if unknown: return f"unknown events: {unknown}", None
    return None, {"pools": norm, "events": list(events)}


@app.post("/jobs/backfill/uniswap_v4_events")
async def backfill_uniswap_v4_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_UNISWAP_V4_EVENTS, _extract_uniswap_v4_events)


_AERO_VALID_EVENTS = ("swap", "deposit", "withdraw", "collect")


def _extract_aero_events(body):
    pools = body.get("pools")
    if pools is None:
        pools = [[c, s0, s1, ts] for (c, s0, s1, ts) in config.AERO_POOLS]
    if not pools or not isinstance(pools, list):
        return "missing pools (list of [chain, sym0, sym1, tick_spacing])", None
    norm = []
    for p in pools:
        if not isinstance(p, list) or len(p) != 4:
            return "each pool must be [chain, sym0, sym1, tick_spacing]", None
        try:
            ts = int(p[3])
        except (TypeError, ValueError):
            return "tick_spacing must be an int", None
        sym0, sym1 = sorted([str(p[1]).upper(), str(p[2]).upper()])
        norm.append([str(p[0]).upper(), sym0, sym1, ts])
    events = body.get("events") or list(_AERO_VALID_EVENTS)
    if not isinstance(events, list): return "events must be a list", None
    unknown = [e for e in events if e not in _AERO_VALID_EVENTS]
    if unknown: return f"unknown events: {unknown}", None
    return None, {"pools": norm, "events": list(events)}


@app.post("/jobs/backfill/aero_events")
async def backfill_aero_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AERO_EVENTS, _extract_aero_events)


_AERO_BASIC_VALID_EVENTS = ("swap", "deposit", "withdraw", "claim")


def _extract_aero_basic_events(body):
    pools = body.get("pools")
    if pools is None:
        pools = [[c, s0, s1, st] for (c, s0, s1, st) in config.AERO_BASIC_POOLS]
    if not pools or not isinstance(pools, list):
        return "missing pools (list of [chain, sym0, sym1, stable])", None
    norm = []
    for p in pools:
        if not isinstance(p, list) or len(p) != 4:
            return "each pool must be [chain, sym0, sym1, stable]", None
        sym0, sym1 = sorted([str(p[1]).upper(), str(p[2]).upper()])
        norm.append([str(p[0]).upper(), sym0, sym1, bool(p[3])])
    events = body.get("events") or list(_AERO_BASIC_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _AERO_BASIC_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"pools": norm, "events": list(events)}


@app.post("/jobs/backfill/aero_basic_events")
async def backfill_aero_basic_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AERO_BASIC_EVENTS, _extract_aero_basic_events)


_AAVE_V4_VALID_EVENTS = ("deposit", "withdraw", "borrow", "repay", "liquidation")


def _extract_aave_v4_events(body):
    chains = body.get("chains") or list(config.AAVE_V4_CHAINS)
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    events = body.get("events") or list(_AAVE_V4_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _AAVE_V4_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"chains": [str(c).upper() for c in chains], "events": list(events)}


@app.post("/jobs/backfill/aave_v4_events")
async def backfill_aave_v4_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AAVE_V4_EVENTS, _extract_aave_v4_events)


_MORPHO_VALID_EVENTS = (
    "supply", "withdraw", "borrow", "repay",
    "supply_collateral", "withdraw_collateral", "liquidation",
)


def _extract_morpho_events(body):
    chains = body.get("chains") or list(config.MORPHO_CHAINS)
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    events = body.get("events") or list(_MORPHO_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _MORPHO_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"chains": [str(c).upper() for c in chains], "events": list(events)}


@app.post("/jobs/backfill/morpho_events")
async def backfill_morpho_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_MORPHO_EVENTS, _extract_morpho_events)


_SPARK_VALID_EVENTS = ("deposit", "withdraw", "borrow", "repay", "flashloan", "liquidation")


def _extract_spark_events(body):
    chains = body.get("chains") or list(config.SPARK_CHAINS)
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    events = body.get("events") or list(_SPARK_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _SPARK_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"chains": [str(c).upper() for c in chains], "events": list(events)}


@app.post("/jobs/backfill/spark_events")
async def backfill_spark_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_SPARK_EVENTS, _extract_spark_events)


@app.post("/admin/wallets")
async def admin_wallets(request):
    """Replace the wallets table from a parquet.

    Two modes:
    - Multipart upload: `curl -u admin:pwd -F file=@wallets.parquet …` — file body is
      written to a temp file and loaded.
    - JSON path:        `{"path": "/app/data/wallets.parquet"}` — loads directly from
      an already-mounted file. Path must be readable by the ingestion container.
    """
    # `skip_rematerialize=true` (multipart form field or JSON body field) skips
    # the post-load rematerialize. Useful for tests or bulk imports where you
    # want to batch multiple wallet edits before paying the rewrite cost.
    skip_remat = False
    try:
        if hasattr(request, "form") and request.form is not None:
            v = request.form.get("skip_rematerialize")
            if v is not None:
                skip_remat = str(v).lower() in ("1", "true", "yes")
    except Exception:
        pass
    if not skip_remat and request.json is not None:
        skip_remat = bool(request.json.get("skip_rematerialize", False))

    uploaded = request.files.get("file") if hasattr(request, "files") else None
    if uploaded is not None:
        tmp = tempfile.NamedTemporaryFile(prefix="wallets-upload-", suffix=".parquet", delete=False)
        try:
            tmp.write(uploaded.body)
            tmp.close()
            summary = await load_into_clickhouse(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        if not skip_remat:
            summary["rematerialize"] = await rematerialize_transfers()
        return response.json(summary)

    body = request.json or {}
    path = body.get("path")
    if not path:
        return response.json(
            {"error": "expected multipart 'file' upload OR JSON {\"path\": \"/app/data/...\"}"},
            status=400,
        )
    try:
        summary = await load_into_clickhouse(path)
    except FileNotFoundError:
        return response.json({"error": f"path not found: {path}"}, status=404)
    if not skip_remat:
        summary["rematerialize"] = await rematerialize_transfers()
    return response.json(summary)


@app.post("/admin/wallets/rematerialize")
async def admin_rematerialize(_request):
    """Force a rebuild of the transfers table's wallet-derived columns + skip
    indexes. Use after editing tradernick.wallets directly in ClickHouse (e.g.
    a one-off INSERT to label a single address) — without this, the MATERIALIZED
    columns and set() skip indexes on existing rows still encode the old
    dictionary state, and filter queries silently return no data for the new
    label.

    Mutations are dispatched async; GET /admin/wallets/rematerialize/status to
    poll progress.
    """
    return response.json(await rematerialize_transfers())


@app.get("/admin/wallets/rematerialize/status")
async def admin_rematerialize_status(_request):
    return response.json(await rematerialize_status())
