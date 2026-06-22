import base64
import hmac
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone

from sanic import Sanic, response

from scripts.bootstrap_wallets import (
    exchange_flow_refresh_status,
    load_into_clickhouse,
    refresh_exchange_flow,
    rematerialize_status,
    rematerialize_transfers,
)

import config
from jobs.manager import (
    JOB_TYPE_BACKFILL_BOOK_DEPTH,
    JOB_TYPE_BACKFILL_BTC_TRANSFERS,
    JOB_TYPE_BACKFILL_EXCHANGE_FLOW_MINUTE,
    JOB_TYPE_BACKFILL_TRANSFERS_REMATERIALIZE,
    JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS,
    JOB_TYPE_BACKFILL_EVM_NATIVE_TRANSFERS,
    JOB_TYPE_BACKFILL_FUNDING_RATE,
    JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS,
    JOB_TYPE_BACKFILL_OHLCV,
    JOB_TYPE_BACKFILL_OPEN_INTEREST,
    JOB_TYPE_BACKFILL_RAW_TRADES,
    JOB_TYPE_BACKFILL_SPOT_OHLCV,
    JOB_TYPE_BACKFILL_SPOT_RAW_TRADES,
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
    JOB_TYPE_BACKFILL_GMX_EVENTS,
    JOB_TYPE_BACKFILL_HYPERLIQUID_EVENTS,
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
    import provider_registry as pr
    log.info("ingestion startup: %s", pr.describe())
    role = pr.current_role()

    app_.ctx.supervisor = Supervisor()
    # start_from_registry is itself role-aware — backfill/admin services
    # short-circuit inside it and walk away without spawning anything.
    await app_.ctx.supervisor.start_from_registry()

    app_.ctx.jobs = JobManager()
    # Run resume in the background with retries. On PC reboot the
    # backfill containers race ClickHouse for DNS resolution / readiness;
    # the first attempt routinely fails with gaierror. Without retries,
    # in-flight jobs are silently abandoned and become zombies.
    app_.add_task(_resume_inflight_with_retry(app_.ctx.jobs))

    # Self-heal for exchange_flow_minute and the 6 HL derived rollups now
    # lives in the unified `data_processor.live` stream (registered in
    # streams/__init__.py as `data_process.processor_live`). No app-side
    # background loop is needed — the supervisor spawns the stream just
    # like any other registered worker, and it tiers its rebuild cadence
    # per-materializer from data_processor.registry.REGISTRY.


async def _resume_inflight_with_retry(jobs):
    """Drive `jobs.resume_inflight()` from a background task with
    exponential backoff. On container restart (especially PC reboot)
    ClickHouse may not be reachable for the first several seconds —
    DNS resolution returns NXDOMAIN, or the port hasn't bound yet.
    Without retries, in-flight rows are silently abandoned and the
    jobs become zombies (status='running', subprocess_alive=false).

    The loop exits on first success. We keep retrying for ~15 minutes
    of total wall-clock; beyond that something is very wrong and the
    operator should look at the logs."""
    import asyncio
    delay = 2.0
    attempt = 0
    deadline = 60 * 15  # seconds of total retry budget
    elapsed = 0.0
    while True:
        attempt += 1
        try:
            await jobs.resume_inflight()
            log.info("resume_inflight succeeded on attempt %d", attempt)
            return
        except Exception as exc:  # noqa: BLE001
            if elapsed >= deadline:
                log.exception(
                    "resume_inflight failed after %d attempts (%.0fs); giving up — "
                    "in-flight jobs will be left as zombies", attempt, elapsed)
                return
            log.warning("resume_inflight attempt %d failed (%s); retrying in %.1fs",
                        attempt, exc.__class__.__name__, delay)
            await asyncio.sleep(delay)
            elapsed += delay
            delay = min(delay * 1.6, 30.0)


@app.get("/health")
async def health(_request):
    return response.json({"ok": True})


@app.get("/config/token_batches")
async def get_token_batches(_request):
    """Ingestion token batches — Batch 1 (original roster) plus any later
    INGEST_TOKENS_BATCH_N. The admin backfill UI uses this to let an operator
    target a specific batch instead of all-or-none. Live jobs always poll the
    union of every batch; the trading dashboard never sees this."""
    return response.json({
        "batches": [
            {"name": name, "tokens": toks, "count": len(toks)}
            for name, toks in config.INGEST_TOKEN_BATCHES
        ],
    })


@app.get("/gaps/calendar")
async def get_gaps_calendar(request):
    """Per-event coverage calendar — powers the FillBoard UI.

    Query params:
      event (required)  StreamSpec.name (e.g. 'aave_v3.deposit')
      since (optional)  default = now - 180d
      until (optional)  default = tomorrow 00:00 UTC (so today's hours
                        show up in `today_hours`)

    Returns past-days array + today-hours strip + first/last data
    dates. See gap_detection.find_calendar() for the classification
    contract."""
    import gap_detection
    import provider_registry as pr
    event_key = request.args.get("event")
    if not event_key:
        return response.json({"error": "missing required ?event=..."}, status=400)
    spec = gap_detection.CALENDAR_EVENTS.get(event_key)
    if spec is None:
        return response.json({"error": f"no calendar spec for event {event_key!r}"},
                             status=404)
    # Per-provider service rejects events that don't belong to it (the
    # admin_server gateway routes events to the correct provider; this
    # is the defence-in-depth check). Monolith mode accepts anything.
    container_provider = pr.current_provider()
    if container_provider is not None and spec.provider != container_provider:
        return response.json(
            {"error": f"event {event_key!r} belongs to provider {spec.provider!r}; "
                      f"this container hosts {container_provider!r}"},
            status=400,
        )
    now = datetime.utcnow().replace(microsecond=0)
    tomorrow_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    raw_since = request.args.get("since")
    raw_until = request.args.get("until")
    try:
        since_dt = (
            datetime.fromisoformat(raw_since.replace("Z", "+00:00"))
            .astimezone(timezone.utc).replace(tzinfo=None)
            if raw_since else now - timedelta(days=180)
        )
        until_dt = (
            datetime.fromisoformat(raw_until.replace("Z", "+00:00"))
            .astimezone(timezone.utc).replace(tzinfo=None)
            if raw_until else tomorrow_midnight
        )
    except (TypeError, ValueError) as exc:
        return response.json({"error": f"invalid since/until: {exc}"}, status=400)
    if since_dt >= until_dt:
        return response.json({"error": "since must be earlier than until"}, status=400)
    chain = request.args.get("chain") or None
    raw_chains = request.args.get("chains") or ""
    chains_list = [c for c in (s.strip() for s in raw_chains.split(",")) if c] or None
    result = await gap_detection.find_calendar(event_key, since_dt, until_dt,
                                                chain=chain, chains=chains_list)
    return response.json(result)


@app.get("/gaps")
async def get_gaps(request):
    """Find days with materially-below-expected row counts for this
    container's provider. Window defaults to the last 30 days.

    Query params:
      since (ISO 8601, optional)  default = now - 30d
      until (ISO 8601, optional)  default = now

    Returns gap rows from every table the provider's gap-spec catalogue
    knows about. See `gap_detection.GAP_SPECS` for the per-table rules.
    """
    import gap_detection
    import provider_registry as pr
    provider = pr.current_provider()
    if provider is None:
        # Monolith mode — caller must pass ?provider=NAME.
        provider = request.args.get("provider")
        if not provider:
            return response.json(
                {"error": "monolith /gaps requires ?provider=NAME "
                          "(per-provider services derive it from INGESTION_PROVIDER)"},
                status=400,
            )
    raw_since = request.args.get("since")
    raw_until = request.args.get("until")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        if raw_since:
            since_dt = datetime.fromisoformat(raw_since.replace("Z", "+00:00")) \
                .astimezone(timezone.utc).replace(tzinfo=None)
        else:
            since_dt = now - timedelta(days=30)
        if raw_until:
            until_dt = datetime.fromisoformat(raw_until.replace("Z", "+00:00")) \
                .astimezone(timezone.utc).replace(tzinfo=None)
        else:
            until_dt = now
    except (TypeError, ValueError) as exc:
        return response.json({"error": f"invalid since/until: {exc}"}, status=400)
    if since_dt >= until_dt:
        return response.json({"error": "since must be earlier than until"}, status=400)
    result = await gap_detection.find_gaps(provider, since_dt, until_dt)
    return response.json(result)


@app.get("/streams")
async def list_streams(request):
    """Return one row per registered stream, joined with the persisted on/off
    state and the latest tick heartbeat. Each row carries `group` (UI section
    label) so the admin panel can lay out one table per protocol."""
    import ch_status
    from streams import STREAMS

    proc_snapshot = request.app.ctx.supervisor.snapshot()
    try:
        statuses = await ch_status.read_all_status()
        state = await ch_status.read_all_state()
    except Exception as exc:  # noqa: BLE001
        log.exception("read status/state failed: %s", exc)
        statuses = []
        state = {}
    status_by_name = {s["name"]: s for s in statuses}
    spec_by_name = {s.name: s for s in STREAMS}
    out = []
    for name, snap in proc_snapshot.items():
        spec = spec_by_name.get(name)
        out.append({
            "name": name,
            "group": spec.group if spec else "Other",
            "cadence_s": spec.cadence_s if spec else None,
            **snap,
            "enabled": state.get(name, True),
            "status": status_by_name.get(name, {}),
        })
    out.sort(key=lambda r: (r["group"], r["name"]))
    return response.json({"streams": out})


@app.post("/streams/<name>/start")
async def stream_start(request, name: str):
    res = await request.app.ctx.supervisor.admin_start(name)
    return response.json(res, status=200 if res.get("ok") else 404)


@app.post("/streams/<name>/stop")
async def stream_stop(request, name: str):
    res = await request.app.ctx.supervisor.admin_stop(name)
    return response.json(res, status=200 if res.get("ok") else 404)


@app.post("/streams/<name>/restart")
async def stream_restart(request, name: str):
    res = await request.app.ctx.supervisor.admin_restart(name)
    return response.json(res, status=200 if res.get("ok") else 404)


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


@app.post("/jobs/clear-finished")
async def clear_finished_jobs(request):
    """Hard-delete finished (non-running) job rows. Used by the admin
    panel's 'Clear finished' button to drop completed / failed /
    cancelled jobs and reduce table clutter. Running jobs are preserved.

    Optional JSON body `{"job_types": ["backfill_x", ...]}` scopes the
    deletion to those types — used by per-provider backfill pages so the
    button only clears jobs visible on that page. Empty / missing body
    deletes across every job type (the overview page's behaviour)."""
    from clickhouse import async_client
    job_types: list[str] = []
    try:
        body = request.json or {}
        raw = body.get("job_types")
        if isinstance(raw, list):
            job_types = [str(x) for x in raw if isinstance(x, str)]
    except Exception:
        pass
    ch = await async_client()
    where = "status != 'running'"
    params: dict = {}
    if job_types:
        where += " AND job_type IN {types:Array(String)}"
        params["types"] = job_types
    pre = await ch.query(
        f"SELECT count() FROM tradernick.ingestion_jobs FINAL WHERE {where}",
        parameters=params,
    )
    n = int(pre.result_rows[0][0]) if pre.result_rows else 0
    await ch.command(
        f"ALTER TABLE tradernick.ingestion_jobs DELETE WHERE {where} "
        "SETTINGS mutations_sync=2",
        parameters=params,
    )
    return response.json({"ok": True, "deleted": n})


_MAX_BACKFILL_DAYS = 365


def _parse_backfill_window(body: dict):
    """Parse + validate {since, until?} from a backfill request body.

    Returns (since_dt, until_dt, None) on success or (None, None, error_str)
    on failure. since is REQUIRED — there is no default. This is the
    crucial fence against accidental long backfills: every call must
    explicitly opt into its time window.

    Format: ISO-8601 (UTC). Trailing 'Z' or '+00:00' both accepted.
    until defaults to the current UTC minute (matches the live tick
    boundary so the window is contiguous with live polling).
    """
    raw_since = body.get("since")
    raw_until = body.get("until")
    if not raw_since:
        return None, None, "missing 'since' (ISO 8601 UTC timestamp). Example: '2026-05-31T00:00:00Z'"

    def _parse(s: str) -> datetime:
        return (
            datetime.fromisoformat(s.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    try:
        since_dt = _parse(raw_since)
    except (TypeError, ValueError):
        return None, None, f"invalid 'since' (must be ISO 8601); got {raw_since!r}"

    if raw_until:
        try:
            until_dt = _parse(raw_until)
        except (TypeError, ValueError):
            return None, None, f"invalid 'until' (must be ISO 8601); got {raw_until!r}"
    else:
        until_dt = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)

    if since_dt >= until_dt:
        return None, None, f"'since' must be earlier than 'until' ({since_dt.isoformat()} >= {until_dt.isoformat()})"
    span_days = (until_dt - since_dt).total_seconds() / 86400.0
    if span_days > _MAX_BACKFILL_DAYS:
        return None, None, f"window too wide ({span_days:.1f}d > {_MAX_BACKFILL_DAYS}d cap)"
    return since_dt, until_dt, None


async def _create_backfill(request, job_type: str):
    """Token-based backfill (binance_*). Body: {since, until?, tokens?, force?}."""
    body = request.json or {}
    tokens = body.get("tokens") or config.INGEST_TOKENS
    force = bool(body.get("force", False))
    if not tokens:
        return response.json({"error": "no tokens"}, status=400)
    since_dt, until_dt, err = _parse_backfill_window(body)
    if err:
        return response.json({"error": err}, status=400)
    try:
        job = await request.app.ctx.jobs.create_backfill_args(
            job_type, since_dt, until_dt, {"tokens": tokens, "force": force}
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


@app.post("/jobs/backfill/binance_spot_ohlcv")
async def backfill_spot_ohlcv(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_SPOT_OHLCV)


@app.post("/jobs/backfill/binance_spot_raw_trades")
async def backfill_spot_raw_trades(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_SPOT_RAW_TRADES)


@app.post("/jobs/backfill/binance_open_interest")
async def backfill_open_interest(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_OPEN_INTEREST)


@app.post("/jobs/backfill/binance_long_short_ratios")
async def backfill_long_short_ratios(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_LONG_SHORT_RATIOS)


@app.post("/jobs/backfill/binance_funding_rate")
async def backfill_funding_rate(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_FUNDING_RATE)


@app.post("/jobs/backfill/binance_book_depth")
async def backfill_book_depth(request):
    return await _create_backfill(request, JOB_TYPE_BACKFILL_BOOK_DEPTH)


# data_process backfills are one-shot operations that don't take a time
# window — they rebuild a target table FROM the current state of the
# source. We bypass _parse_backfill_window so the dashboard can fire
# them with an empty body.
async def _create_unwindowed_backfill(request, job_type: str, args_extra: dict | None = None):
    body = request.json or {}
    args_extra = {**(args_extra or {}), **{k: v for k, v in body.items()
                                            if k in {"refresh_exchange_flow",
                                                     "table"}}}
    args_extra["force"] = bool(body.get("force", False))
    # Use sentinel since/until equal to now so existing dashboards that
    # display the window column don't render '?' — the column is just
    # "when the job was created".
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
    try:
        job = await request.app.ctx.jobs.create_backfill_args(
            job_type, now, now, args_extra,
        )
    except RuntimeError as exc:
        return response.json({"error": str(exc)}, status=429)
    except ValueError as exc:
        return response.json({"error": str(exc)}, status=400)
    return response.json(job, status=202)


@app.post("/jobs/backfill/exchange_flow_minute")
async def backfill_exchange_flow_minute(request):
    # Now a windowed backfill — under the hood the job module forwards to
    # data_processor.backfill which walks the affected hourly partitions
    # in [since, until). Without a real window every job would complete
    # instantly with progress=1.0 (no partitions in zero-width range).
    return await _create_transfer_backfill(
        request, JOB_TYPE_BACKFILL_EXCHANGE_FLOW_MINUTE, _extract_empty,
    )


@app.post("/jobs/backfill/transfers_rematerialize")
async def backfill_transfers_rematerialize(request):
    return await _create_unwindowed_backfill(
        request, JOB_TYPE_BACKFILL_TRANSFERS_REMATERIALIZE,
    )


# Unified materializer backfill — single endpoint for every derived table
# (exchange_flow_minute + 6 HL aggregates). Body shape:
#   {since, until?, force?, materializers: [...]}
# `materializers` is a list of names from data_processor.registry.ALL_NAMES.
@app.post("/jobs/backfill/data_processor")
async def backfill_data_processor(request):
    from jobs.manager import JOB_TYPE_BACKFILL_DATA_PROCESSOR
    body = request.json or {}
    materializers = body.get("materializers")
    if not isinstance(materializers, list) or not materializers:
        return response.json({"error": "missing materializers (list)"}, status=400)
    materializers = [str(m) for m in materializers]
    force = bool(body.get("force", False))
    since_dt, until_dt, err = _parse_backfill_window(body)
    if err:
        return response.json({"error": err}, status=400)
    args_extra = {"materializers": materializers, "force": force}
    try:
        job = await request.app.ctx.jobs.create_backfill_args(
            JOB_TYPE_BACKFILL_DATA_PROCESSOR, since_dt, until_dt, args_extra,
        )
    except RuntimeError as exc:
        return response.json({"error": str(exc)}, status=429)
    return response.json(job, status=202)


async def _create_transfer_backfill(request, job_type: str, extract_args):
    """Generic (non-token) backfill. Body: {since, until?, force?, …extras}."""
    body = request.json or {}
    force = bool(body.get("force", False))
    since_dt, until_dt, err = _parse_backfill_window(body)
    if err:
        return response.json({"error": err}, status=400)
    err, args_extra = extract_args(body)
    if err:
        return response.json({"error": err}, status=400)
    args_extra["force"] = force
    try:
        job = await request.app.ctx.jobs.create_backfill_args(job_type, since_dt, until_dt, args_extra)
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


def _extract_erc20_chains(body):
    """Backfill input shape for the simplified ERC-20 form: just `chains`.
    Tokens come from config.EVM_ERC20_BY_CHAIN (same roster the live job
    polls), so the backfill mirrors the live job by default."""
    chains = body.get("chains")
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    return None, {"chains": [str(c).upper() for c in chains]}


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
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_EVM_ERC20_TRANSFERS, _extract_erc20_chains)


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


@app.post("/jobs/backfill/aave_v3_events")
async def backfill_aave_v3_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AAVE_EVENTS, _extract_aave_events)


_UNI_VALID_EVENTS = ("swap", "deposit", "withdraw", "collect")


def _extract_uniswap_events(body):
    pools = body.get("pools")
    if not pools:
        # Match the live worker: UNI_V3_LIVE_POOLS narrows to a tight
        # high-volume set, falling back to the full UNI_V3_POOLS catalogue.
        # Caller can still supply explicit `pools` to override.
        src = config.UNI_V3_LIVE_POOLS or config.UNI_V3_POOLS
        pools = [[c, s0, s1, fee] for (c, s0, s1, fee) in src]
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


@app.post("/jobs/backfill/uniswap_v3_events")
async def backfill_uniswap_v3_events(request):
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
    if not pools:
        # Match the live worker: UNI_V2_LIVE_POOLS narrows to high-volume,
        # falling back to UNI_V2_POOLS.
        src = config.UNI_V2_LIVE_POOLS or config.UNI_V2_POOLS
        pools = [[c, s0, s1] for (c, s0, s1) in src]
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
    if not pools:
        # Match the live worker: UNI_V4_LIVE_POOLS narrows to high-volume,
        # falling back to UNI_V4_POOLS.
        src = config.UNI_V4_LIVE_POOLS or config.UNI_V4_POOLS
        pools = [[c, s0, s1, fee, ts, hk] for (c, s0, s1, fee, ts, hk) in src]
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
    if not pools:
        # Match the live worker: AERO_LIVE_POOLS narrows to high-volume,
        # falling back to AERO_POOLS.
        src = config.AERO_LIVE_POOLS or config.AERO_POOLS
        pools = [[c, s0, s1, ts] for (c, s0, s1, ts) in src]
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


@app.post("/jobs/backfill/aero_concentrated_events")
async def backfill_aero_concentrated_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_AERO_EVENTS, _extract_aero_events)


_AERO_BASIC_VALID_EVENTS = ("swap", "deposit", "withdraw", "claim")


def _extract_aero_basic_events(body):
    pools = body.get("pools")
    if not pools:
        # Match the live worker: AERO_BASIC_LIVE_POOLS narrows to high-volume,
        # falling back to AERO_BASIC_POOLS.
        src = config.AERO_BASIC_LIVE_POOLS or config.AERO_BASIC_POOLS
        pools = [[c, s0, s1, st] for (c, s0, s1, st) in src]
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


_GMX_VALID_EVENTS = (
    "position_increase", "position_decrease", "liquidation", "swap",
    "deposit", "withdraw", "funding", "borrowing", "fees_collected",
)


def _extract_gmx_events(body):
    chains = body.get("chains") or list(config.GMX_CHAINS)
    if not chains or not isinstance(chains, list):
        return "missing chains (list)", None
    events = body.get("events") or list(_GMX_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _GMX_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    return None, {"chains": [str(c).upper() for c in chains], "events": list(events)}


@app.post("/jobs/backfill/gmx_v2_events")
async def backfill_gmx_v2_events(request):
    return await _create_transfer_backfill(request, JOB_TYPE_BACKFILL_GMX_EVENTS, _extract_gmx_events)


_HL_VALID_EVENTS = (
    "ohlcv", "trades", "fills", "funding", "position_history",
    "trade_history", "transfers", "vaults",
)


def _extract_hyperliquid_events(body):
    tokens = body.get("tokens")
    if tokens is not None and not isinstance(tokens, list):
        return "tokens must be a list", None
    events = body.get("events") or list(_HL_VALID_EVENTS)
    if not isinstance(events, list):
        return "events must be a list", None
    unknown = [e for e in events if e not in _HL_VALID_EVENTS]
    if unknown:
        return f"unknown events: {unknown}", None
    out = {"events": list(events)}
    if tokens is not None:
        out["tokens"] = [str(t).upper() for t in tokens]
    return None, out


@app.post("/jobs/backfill/hyperliquid_events")
async def backfill_hyperliquid_events(request):
    return await _create_transfer_backfill(
        request, JOB_TYPE_BACKFILL_HYPERLIQUID_EVENTS, _extract_hyperliquid_events,
    )


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


@app.post("/admin/exchange-flow/refresh")
async def admin_exchange_flow_refresh(_request):
    """Force a 30-day rebuild of tradernick.exchange_flow_minute.

    Auto-called as the final step of /admin/wallets/rematerialize so the
    rollup stays consistent with the freshly-rewritten sender/receiver
    categories on the transfers table. Exposed standalone here for manual
    re-runs (e.g. after a filter-logic change in data_processor.registry
    that requires a backfill but not a wallet-side rematerialize).

    Under the hood this enqueues a backfill_data_processor job for
    materializers=['exchange_flow_minute'] — the data_processor worker
    rebuilds the affected partitions atomically via REPLACE PARTITION.
    """
    return response.json(await refresh_exchange_flow())


@app.get("/admin/exchange-flow/refresh/status")
async def admin_exchange_flow_refresh_status(_request):
    return response.json(await exchange_flow_refresh_status())
