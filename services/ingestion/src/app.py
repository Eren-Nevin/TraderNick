import base64
import hmac
import logging

from sanic import Sanic, response

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
    if not tokens:
        return response.json({"error": "no tokens"}, status=400)
    if days <= 0 or days > 365:
        return response.json({"error": "days must be in 1..365"}, status=400)
    try:
        job = await request.app.ctx.jobs.create_backfill(job_type, tokens, days)
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
    if days <= 0 or days > 365:
        return response.json({"error": "days must be in 1..365"}, status=400)
    err, args_extra = extract_args(body)
    if err:
        return response.json({"error": err}, status=400)
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
