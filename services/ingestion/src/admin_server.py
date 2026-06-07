"""admin_server — thin Sanic gateway in front of the per-provider services.

The dashboard talks ONLY to this service. admin_server holds no state,
runs no background tasks, and owns no database tables. It just routes
and fans out HTTP calls to the per-provider × {live, backfill} services
defined in docker-compose.yml.

Why a gateway and not a fat dashboard?
- The dashboard codebase shouldn't grow a "provider table" — that's
  ingestion's concern, not UI's.
- Operators / scripts / future CLI tools should be able to bypass the
  gateway and hit a provider service directly (`curl http://spark_live:
  8000/streams`). admin_server going offline shouldn't degrade anything
  but the dashboard's admin page.
- New provider = one PROVIDER_REGISTRY edit + two compose stanzas + two
  env vars here. Zero dashboard work.

API surface mirrors the monolith's admin routes 1:1 so the dashboard's
existing SvelteKit proxy needs no changes.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from sanic import Sanic, response
from sanic.request import Request

import config
import provider_registry as pr

log = logging.getLogger("admin_server")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [admin_server] %(levelname)s %(message)s")

app = Sanic("tradernick_admin_server")
app.config.RESPONSE_TIMEOUT = 60

# --------------------------------------------------------------------------
# Routing table — built from env at startup. The single source of truth
# for "which provider service handles X". URLs that aren't set in the
# env stay None and we fall back to LEGACY_INGESTION_URL — that's what
# lets the gateway keep working while providers are mid-migration off
# the monolith.
# --------------------------------------------------------------------------

LEGACY_INGESTION_URL = os.environ.get("LEGACY_INGESTION_URL", "").rstrip("/")


def _env_url(name: str) -> str | None:
    v = os.environ.get(name, "").strip().rstrip("/")
    return v or None


# provider → {"live": url, "backfill": url}.  data_process is treated
# uniformly with the DeFiStream providers — it has live + backfill URLs.
PROVIDER_URLS: dict[str, dict[str, str | None]] = {
    "hyperliquid":  {"live": _env_url("HYPERLIQUID_LIVE_URL"),  "backfill": _env_url("HYPERLIQUID_BACKFILL_URL")},
    "binance":      {"live": _env_url("BINANCE_LIVE_URL"),      "backfill": _env_url("BINANCE_BACKFILL_URL")},
    "transfers":    {"live": _env_url("TRANSFERS_LIVE_URL"),    "backfill": _env_url("TRANSFERS_BACKFILL_URL")},
    "aave":         {"live": _env_url("AAVE_LIVE_URL"),         "backfill": _env_url("AAVE_BACKFILL_URL")},
    "uniswap":      {"live": _env_url("UNISWAP_LIVE_URL"),      "backfill": _env_url("UNISWAP_BACKFILL_URL")},
    "aerodrome":    {"live": _env_url("AERODROME_LIVE_URL"),    "backfill": _env_url("AERODROME_BACKFILL_URL")},
    "lido":         {"live": _env_url("LIDO_LIVE_URL"),         "backfill": _env_url("LIDO_BACKFILL_URL")},
    "morpho":       {"live": _env_url("MORPHO_LIVE_URL"),       "backfill": _env_url("MORPHO_BACKFILL_URL")},
    "spark":        {"live": _env_url("SPARK_LIVE_URL"),        "backfill": _env_url("SPARK_BACKFILL_URL")},
    "gmx":          {"live": _env_url("GMX_LIVE_URL"),          "backfill": _env_url("GMX_BACKFILL_URL")},
    "data_process": {"live": _env_url("DATA_PROCESS_LIVE_URL"), "backfill": _env_url("DATA_PROCESS_BACKFILL_URL")},
}

TRADERNICK_ADMIN_URL = _env_url("TRADERNICK_ADMIN_URL")


def _stream_to_provider() -> dict[str, str]:
    """Build {stream_name: provider} at startup. Imports `streams` for the
    registry only — that module is pure dataclass + list, no DS or CH
    side effects, so this is cheap and side-effect-free."""
    from streams import STREAMS  # local import keeps module load light
    out: dict[str, str] = {}
    for s in STREAMS:
        provider = pr.GROUP_TO_PROVIDER.get(s.group)
        if provider:
            out[s.name] = provider
    return out


STREAM_TO_PROVIDER: dict[str, str] = _stream_to_provider()
JOB_TYPE_TO_PROVIDER: dict[str, str] = pr.JOB_TYPE_TO_PROVIDER


def _resolve_live_url(provider: str | None) -> str | None:
    if provider and provider in PROVIDER_URLS:
        return PROVIDER_URLS[provider]["live"] or LEGACY_INGESTION_URL or None
    return LEGACY_INGESTION_URL or None


def _resolve_backfill_url(provider: str | None) -> str | None:
    if provider and provider in PROVIDER_URLS:
        return PROVIDER_URLS[provider]["backfill"] or LEGACY_INGESTION_URL or None
    return LEGACY_INGESTION_URL or None


def _live_url_to_providers() -> dict[str, list[str]]:
    """Group providers by the URL their live calls go to. During the
    migration most provider URLs default to the monolith URL (one URL
    serves many providers) and the few cutover providers have their
    own URL — so we query each unique URL once and use the provider
    set to filter the response. Avoids both N-way duplication AND
    redundant calls when multiple providers share an upstream."""
    out: dict[str, list[str]] = {}
    for provider, urls in PROVIDER_URLS.items():
        url = urls["live"] or LEGACY_INGESTION_URL
        if url:
            out.setdefault(url, []).append(provider)
    return out


def _backfill_url_to_providers() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for provider, urls in PROVIDER_URLS.items():
        url = urls["backfill"] or LEGACY_INGESTION_URL
        if url:
            out.setdefault(url, []).append(provider)
    return out


# Pre-compute provider → owned stream names + owned job_types. Used by
# the fan-out filter so a shared URL (e.g. monolith covering 9
# not-yet-cutover providers) gets its response sliced per-provider.
def _build_owned_indices() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    streams_by_provider: dict[str, set[str]] = {}
    for stream_name, provider in STREAM_TO_PROVIDER.items():
        streams_by_provider.setdefault(provider, set()).add(stream_name)
    jobs_by_provider: dict[str, set[str]] = {}
    for jt, provider in JOB_TYPE_TO_PROVIDER.items():
        jobs_by_provider.setdefault(provider, set()).add(jt)
    return streams_by_provider, jobs_by_provider


STREAMS_BY_PROVIDER, JOB_TYPES_BY_PROVIDER = _build_owned_indices()


# --------------------------------------------------------------------------
# Basic auth — same envs as everything else. We validate the incoming
# header here AND forward it verbatim to provider services so the
# downstream auth checks (their own basic_auth middleware) pass too.
# --------------------------------------------------------------------------

UNAUTH_PATHS = {"/health"}


def _check_basic_auth(request: Request) -> bool:
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
            headers={"WWW-Authenticate": 'Basic realm="tradernick-admin"'},
        )


# --------------------------------------------------------------------------
# HTTPX client lifecycle. One async client across all requests.
# --------------------------------------------------------------------------

@app.before_server_start
async def _startup(app_, _loop):
    # Gap-calendar calls against transfers / hl_fills take ~4s solo on a
    # 971M-row table and >15s under 5-way concurrency from a single
    # FillBoardSection mount. 60s leaves headroom; the semaphore below
    # caps actual concurrency so we don't have to rely on it.
    app_.ctx.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
    # Per-provider semaphore for /gaps/calendar forwards. The dashboard
    # fires one fetch per FillBoard simultaneously on mount, which
    # blasts the backfill service with N concurrent gap_detection
    # passes — and gap_detection runs 3 large CH scans per call. The
    # semaphore queues calls per provider (max 2 inflight) so CH
    # contention stays bounded and the 4th/5th request returns at
    # ~2× single latency instead of 5× under uncoordinated load.
    app_.ctx.gap_calendar_semaphores = {
        provider: asyncio.Semaphore(2) for provider in PROVIDER_URLS
    }
    log.info("admin_server up. providers=%s legacy=%s",
             {k: bool(v["live"]) for k, v in PROVIDER_URLS.items()},
             LEGACY_INGESTION_URL or "<unset>")


@app.after_server_stop
async def _shutdown(app_, _loop):
    client = getattr(app_.ctx, "http", None)
    if client is not None:
        await client.aclose()


# --------------------------------------------------------------------------
# Forwarding primitives.
# --------------------------------------------------------------------------

def _auth_header(request: Request) -> dict[str, str]:
    auth = request.headers.get("authorization")
    return {"Authorization": auth} if auth else {}


async def _fetch_one(client: httpx.AsyncClient, url: str, path: str,
                     auth: dict[str, str], query: str = "") -> tuple[int, Any]:
    """GET <url><path>?<query>. Returns (status, parsed json or raw text)."""
    full = f"{url}{path}"
    if query:
        full = f"{full}?{query}"
    try:
        resp = await client.get(full, headers=auth)
    except httpx.HTTPError as exc:
        return 599, {"error": f"upstream fetch failed: {exc}"}
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"error": "non-json response", "body": resp.text[:500]}


async def _forward(request: Request, method: str, url: str, path: str,
                   *, body: Any = None) -> response.HTTPResponse:
    """POST/DELETE pass-through. Forwards the basic-auth header and body."""
    client = request.app.ctx.http
    full = f"{url}{path}"
    headers = _auth_header(request)
    try:
        if method == "POST":
            resp = await client.post(full, headers=headers, json=body)
        elif method == "DELETE":
            resp = await client.delete(full, headers=headers)
        elif method == "GET":
            resp = await client.get(full, headers=headers)
        else:
            return response.json({"error": f"unsupported method {method}"}, status=500)
    except httpx.HTTPError as exc:
        return response.json({"error": f"upstream {method} failed: {exc}"}, status=599)
    try:
        return response.json(resp.json(), status=resp.status_code)
    except Exception:
        return response.text(resp.text, status=resp.status_code)


# --------------------------------------------------------------------------
# Routes — mirror the monolith's admin API exactly. Dashboard code is
# untouched: it just sees a service that behaves the same way.
# --------------------------------------------------------------------------

@app.get("/health")
async def health(_request):
    return response.json({"ok": True, "service": "admin_server"})


@app.get("/streams")
async def list_streams(request):
    """Query each unique upstream URL once and slice the response by the
    providers that URL serves. During migration, the monolith URL
    serves every not-yet-cutover provider — so a single GET to
    monolith yields rows for hyperliquid/binance/aave/... while the
    spark URL (after cutover) yields rows for spark only. No
    duplicates regardless of how many providers a URL serves."""
    client = request.app.ctx.http
    auth = _auth_header(request)
    url_to_providers = _live_url_to_providers()
    urls = list(url_to_providers.keys())
    results = await asyncio.gather(
        *[_fetch_one(client, url, "/streams", auth) for url in urls],
        return_exceptions=False,
    )
    streams: list[dict] = []
    errors: list[dict] = []
    for url, (status, body) in zip(urls, results):
        providers = url_to_providers[url]
        if status >= 400 or not isinstance(body, dict):
            errors.append({"providers": providers, "url": url, "status": status,
                           "error": body.get("error") if isinstance(body, dict) else str(body)})
            continue
        chunk = body.get("streams") if isinstance(body, dict) else None
        if not isinstance(chunk, list):
            continue
        # Take only the streams this URL is responsible for.
        owned: set[str] = set()
        for p in providers:
            owned |= STREAMS_BY_PROVIDER.get(p, set())
        for s in chunk:
            if s.get("name") in owned:
                streams.append(s)
    streams.sort(key=lambda r: (r.get("group", ""), r.get("name", "")))
    return response.json({"streams": streams, "errors": errors})


@app.post("/streams/<name>/<action>")
async def stream_action(request, name: str, action: str):
    if action not in ("start", "stop", "restart"):
        return response.json({"error": f"unknown action {action}"}, status=400)
    provider = STREAM_TO_PROVIDER.get(name)
    url = _resolve_live_url(provider)
    if not url:
        return response.json({"error": f"no live URL for stream {name} "
                                       f"(provider={provider or 'unknown'})"},
                             status=502)
    return await _forward(request, "POST", url, f"/streams/{name}/{action}")


@app.get("/jobs")
async def list_jobs(request):
    """Same dedup-by-URL + slice-by-provider model as /streams. Filters
    by job_type so a shared monolith URL doesn't produce duplicates of
    job rows whose job_type maps to a now-cutover provider."""
    client = request.app.ctx.http
    auth = _auth_header(request)
    raw_limit = request.args.get("limit", "100")
    try:
        limit = max(1, min(int(raw_limit), 500))
    except ValueError:
        limit = 100
    query = urlencode({"limit": limit})
    url_to_providers = _backfill_url_to_providers()
    urls = list(url_to_providers.keys())
    results = await asyncio.gather(
        *[_fetch_one(client, url, "/jobs", auth, query=query) for url in urls]
    )
    jobs: list[dict] = []
    errors: list[dict] = []
    for url, (status, body) in zip(urls, results):
        providers = url_to_providers[url]
        if status >= 400:
            errors.append({"providers": providers, "url": url, "status": status,
                           "error": body.get("error") if isinstance(body, dict) else str(body)})
            continue
        chunk = body if isinstance(body, list) else (body.get("jobs") if isinstance(body, dict) else None)
        if not isinstance(chunk, list):
            continue
        owned: set[str] = set()
        for p in providers:
            owned |= JOB_TYPES_BY_PROVIDER.get(p, set())
        for j in chunk:
            if j.get("job_type") in owned:
                jobs.append(j)
    jobs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    jobs = jobs[:limit]
    return response.json({"jobs": jobs, "errors": errors})


@app.get("/jobs/<job_id>")
async def get_job(request, job_id: str):
    """Query each unique backfill URL once; pick the response with
    subprocess_alive=true (that's the owning service)."""
    client = request.app.ctx.http
    auth = _auth_header(request)
    urls = list(_backfill_url_to_providers().keys())
    results = await asyncio.gather(
        *[_fetch_one(client, url, f"/jobs/{job_id}", auth) for url in urls]
    )
    fallback: tuple[int, Any] | None = None
    for status, body in results:
        if status == 200 and isinstance(body, dict):
            if body.get("subprocess_alive"):
                return response.json(body, status=200)
            if fallback is None:
                fallback = (status, body)
    if fallback is not None:
        return response.json(fallback[1], status=fallback[0])
    return response.json({"error": "not found"}, status=404)


@app.delete("/jobs/<job_id>")
async def cancel_job(request, job_id: str):
    """GET first to learn the job_type, then DELETE on the owning
    backfill service (resolved via JOB_TYPE_TO_PROVIDER)."""
    client = request.app.ctx.http
    auth = _auth_header(request)
    urls = list(_backfill_url_to_providers().keys())
    job_type: str | None = None
    for url in urls:
        status, body = await _fetch_one(client, url, f"/jobs/{job_id}", auth)
        if status == 200 and isinstance(body, dict):
            job_type = body.get("job_type")
            if job_type:
                break
    if not job_type:
        return response.json({"error": f"job {job_id} not found on any backfill service"},
                             status=404)
    provider = JOB_TYPE_TO_PROVIDER.get(job_type)
    url = _resolve_backfill_url(provider)
    if not url:
        return response.json({"error": f"no backfill URL for job_type {job_type} "
                                       f"(provider={provider or 'unknown'})"},
                             status=502)
    return await _forward(request, "DELETE", url, f"/jobs/{job_id}")


@app.post("/jobs/backfill/<job_type>")
async def create_backfill(request, job_type: str):
    """Route by JOB_TYPE_TO_PROVIDER → owning backfill service."""
    full_jt = job_type if job_type.startswith("backfill_") else f"backfill_{job_type}"
    provider = (JOB_TYPE_TO_PROVIDER.get(full_jt)
                or JOB_TYPE_TO_PROVIDER.get(f"backfill_{job_type}"))
    url = _resolve_backfill_url(provider)
    if not url:
        return response.json({"error": f"no backfill URL for job_type {job_type} "
                                       f"(provider={provider or 'unknown'})"},
                             status=502)
    body = request.json or {}
    return await _forward(request, "POST", url, f"/jobs/backfill/{job_type}", body=body)


@app.get("/gaps/calendar")
async def get_gaps_calendar(request):
    """Per-event coverage calendar — routes to the owning provider's
    backfill service based on the event_key's provider.

    Wrapped in a per-provider semaphore: when a FillBoardSection mounts
    it fires one fetch per event (5–8) concurrently, and the underlying
    CH scans on the transfers / hl_fills tables don't parallelise well.
    The semaphore caps inflight calls per provider so the 4th/5th
    request returns at ~2× single latency rather than starving every
    request to a 60s timeout."""
    event_key = request.args.get("event")
    if not event_key:
        return response.json({"error": "missing required ?event=..."}, status=400)
    # Resolve provider via the gap_detection event registry. We import
    # lazily so admin_server can boot without the full ingestion src/
    # parse if the catalogue ever grows runtime side-effects.
    from gap_detection import CALENDAR_EVENTS
    spec = CALENDAR_EVENTS.get(event_key)
    if spec is None:
        return response.json({"error": f"no calendar spec for event {event_key!r}"},
                             status=404)
    url = _resolve_backfill_url(spec.provider)
    if not url:
        return response.json(
            {"error": f"no backfill URL for provider {spec.provider!r}"},
            status=502,
        )
    qs = request.query_string
    sem = request.app.ctx.gap_calendar_semaphores.get(spec.provider)
    if sem is None:
        # Unknown provider — let it through unthrottled rather than 500.
        return await _forward(request, "GET", url, f"/gaps/calendar?{qs}")
    async with sem:
        return await _forward(request, "GET", url, f"/gaps/calendar?{qs}")


@app.get("/gaps")
async def get_gaps(request):
    """Find days with materially-below-expected row counts.

    Query params:
      provider (optional)  if set, route to that provider's backfill
                           service. If unset, fan-out across every
                           backfill URL and aggregate.
      since, until         forwarded verbatim to the backfill /gaps.
    """
    client = request.app.ctx.http
    auth = _auth_header(request)
    qs = request.query_string  # forward since/until + any future args

    target_provider = request.args.get("provider")
    if target_provider:
        url = _resolve_backfill_url(target_provider)
        if not url:
            return response.json(
                {"error": f"no backfill URL for provider {target_provider!r}"},
                status=502,
            )
        return await _forward(request, "GET", url, f"/gaps?{qs}")

    # Fan-out — query each unique backfill URL with its provider list
    # forced via ?provider=. The monolith /gaps requires a provider in
    # the query string; per-provider services use their INGESTION_PROVIDER.
    url_to_providers = _backfill_url_to_providers()
    requests_planned: list[tuple[str, str]] = []  # (provider, url) — one per provider
    for url, providers in url_to_providers.items():
        for provider in providers:
            requests_planned.append((provider, url))

    async def _one(provider: str, url: str):
        # Strip any inbound provider= param so we can set our own.
        base_qs = "&".join(p for p in qs.split("&") if not p.startswith("provider="))
        full_qs = (base_qs + "&" if base_qs else "") + f"provider={provider}"
        return provider, url, await _fetch_one(client, url, "/gaps", auth, query=full_qs)

    results = await asyncio.gather(*[_one(p, u) for p, u in requests_planned])
    all_gaps: list[dict] = []
    errors: list[dict] = []
    for provider, url, (status, body) in results:
        if status >= 400 or not isinstance(body, dict):
            errors.append({"provider": provider, "url": url, "status": status,
                           "error": body.get("error") if isinstance(body, dict) else str(body)})
            continue
        for g in (body.get("gaps") or []):
            g.setdefault("provider", provider)
            all_gaps.append(g)
        for err in (body.get("errors") or []):
            errors.append({"provider": provider, "url": url, **err})
    all_gaps.sort(key=lambda r: (r.get("day", ""), r.get("provider", ""), r.get("table", "")))
    return response.json({"gaps": all_gaps, "errors": errors})


@app.post("/jobs/clear-finished")
async def clear_finished(request):
    """Split the `job_types` payload by owning provider, fan-out one POST
    per affected backfill service, sum the `deleted` counts. Empty
    payload (`{}`) clears across every backfill service in parallel."""
    client = request.app.ctx.http
    auth = _auth_header(request)
    body = request.json or {}
    raw_types = body.get("job_types")
    if not isinstance(raw_types, list) or not raw_types:
        # No scope → broadcast empty body to every unique backfill URL.
        targets = [("__broadcast__", u) for u in _backfill_url_to_providers()]
        bodies = [{} for _ in targets]
    else:
        by_provider: dict[str, list[str]] = {}
        for jt in raw_types:
            provider = JOB_TYPE_TO_PROVIDER.get(jt)
            if provider:
                by_provider.setdefault(provider, []).append(jt)
        targets = []
        bodies = []
        for provider, jts in by_provider.items():
            url = _resolve_backfill_url(provider)
            if url:
                targets.append((provider, url))
                bodies.append({"job_types": jts})

    async def _one(url: str, payload: dict):
        try:
            resp = await client.post(f"{url}/jobs/clear-finished",
                                     headers=auth, json=payload)
            return resp.status_code, resp.json()
        except Exception as exc:
            return 599, {"error": str(exc)}

    results = await asyncio.gather(*[_one(u, b) for (_, u), b in zip(targets, bodies)])
    total = 0
    errors: list[dict] = []
    for (provider, url), (status, payload) in zip(targets, results):
        if status >= 400 or not isinstance(payload, dict):
            errors.append({"provider": provider, "url": url, "status": status,
                           "error": payload.get("error") if isinstance(payload, dict) else str(payload)})
            continue
        total += int(payload.get("deleted", 0))
    return response.json({"ok": True, "deleted": total, "errors": errors})


# --------------------------------------------------------------------------
# Wallets parquet upload + exchange_flow refresh — forward to
# tradernick_admin (during migration) or the legacy monolith (today).
# --------------------------------------------------------------------------

def _admin_url() -> str | None:
    return TRADERNICK_ADMIN_URL or LEGACY_INGESTION_URL or None


@app.route("/admin/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
async def admin_passthrough(request, path: str):
    url = _admin_url()
    if not url:
        return response.json({"error": "no admin URL configured"}, status=502)
    # Forward as-is with body + query string + auth.
    client = request.app.ctx.http
    full = f"{url}/admin/{path}"
    qs = request.query_string
    if qs:
        full = f"{full}?{qs}"
    headers = _auth_header(request)
    # Pass through the body (could be parquet upload — handle as bytes).
    body = request.body
    if request.headers.get("content-type"):
        headers["Content-Type"] = request.headers["content-type"]
    try:
        resp = await client.request(request.method, full, headers=headers, content=body)
    except httpx.HTTPError as exc:
        return response.json({"error": f"upstream admin call failed: {exc}"}, status=599)
    try:
        return response.json(resp.json(), status=resp.status_code)
    except Exception:
        return response.raw(resp.content, status=resp.status_code,
                            content_type=resp.headers.get("content-type", "application/octet-stream"))
