import base64
import hmac
import logging

from sanic import Sanic, response

import config
from jobs.manager import JOB_TYPE_BACKFILL_OHLCV, JOB_TYPE_BACKFILL_RAW_TRADES, JobManager
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
    app_.ctx.supervisor.start(["binance_ohlcv", "binance_raw_trades"])
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
