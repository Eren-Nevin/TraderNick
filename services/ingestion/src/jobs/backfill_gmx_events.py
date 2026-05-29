"""GMX V2 events backfill (ARB-only). 9 events × chains. Same 24h/1.2s/
dead-skip pattern as the other lending drivers."""
import asyncio, json, logging, signal, sys
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream
import config
from clickhouse import GMX_EVENTS, async_client, safe_ident

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill_gmx_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_HOURS = 24
INTER_CHUNK_SLEEP_S = 1.2
RETRY_DELAYS_S = (1.0, 3.0, 8.0, 20.0, 45.0)
_stop = False


def _on_sigterm(*_):
    global _stop; _stop = True


def _utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None)
def _parse_iso(s): return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)
def _iso_z(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def _sql_dt(dt): return dt.strftime("%Y-%m-%d %H:%M:%S")


async def _load_job(job_id):
    ch = await async_client()
    rows = await ch.query(
        "SELECT job_type, args, status, started_at FROM tradernick.ingestion_jobs FINAL WHERE job_id = {j:String}",
        parameters={"j": job_id})
    if not rows.result_rows: raise RuntimeError(f"job {job_id} not found")
    r = rows.result_rows[0]
    return {"job_type": r[0], "args": json.loads(r[1]), "status": r[2], "started_at": r[3]}


async def _write_status(*, job_id, job_type, args, status, progress, started_at, finished_at=None, error=None):
    ch = await async_client()
    await ch.insert("tradernick.ingestion_jobs",
        [[job_id, job_type, json.dumps(args), status, float(progress), started_at, finished_at, error, _utcnow()]],
        column_names=["job_id","job_type","args","status","progress","started_at","finished_at","error","updated_at"])


def _is_rate_limit(exc):
    m = str(exc).lower()
    return "too many requests" in m or "429" in m or "rate limit" in m


def _is_not_supported(exc):
    m = str(exc).lower()
    return ("not found" in m or "not available" in m or "not supported" in m or "not configured" in m)


def _planned_chunks(chains, events, since, until):
    out = []
    step = timedelta(hours=CHUNK_HOURS)
    for c in chains:
        for ev in events:
            t = since
            while t < until:
                t_end = min(t + step, until)
                out.append((c, ev, t, t_end))
                t = t_end
    return out


async def _fetch_chunk(ds, *, chain, event, since, until):
    method_name, table, columns, transform = GMX_EVENTS[event]
    last_exc = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            log.info("rate-limited; backoff %.1fs (attempt %d)", delay, attempt)
            await asyncio.sleep(delay)
        try:
            b = getattr(ds.evm.gmx_v2, method_name)()
            b = b.network(chain).time_range(_iso_z(since), _iso_z(until)).verbose().with_value()
            df = await b.as_df("polars")
            if df.is_empty(): return 0
            rows = transform(df, chain=chain)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit(exc): raise
    raise last_exc


async def main(job_id):
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    if not config.DEFISTREAM_API_KEY: sys.exit(2)
    job = await _load_job(job_id)
    job_type, args, started_at = job["job_type"], job["args"], job["started_at"]
    chains = args.get("chains") or list(config.GMX_CHAINS)
    events = args.get("events") or list(GMX_EVENTS.keys())
    unknown = [e for e in events if e not in GMX_EVENTS]
    if unknown: log.error("unknown events: %s", unknown); sys.exit(2)
    since = _parse_iso(args["since"]); until = _parse_iso(args["until"])
    completed_set = {tuple(k) for k in args.get("completed_chunks", [])}
    chunks = _planned_chunks(chains, events, since, until)
    total = len(chunks)
    done = sum(1 for (c,ev,cs,_) in chunks if (c,ev,cs.isoformat()) in completed_set)
    await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                        progress=(done/total) if total else 1.0, started_at=started_at)
    log.info("job %s starting: chunks=%d resumed_at=%d", job_id, total, done)

    if args.get("force") and done == 0:
        ch = await async_client()
        for ev in events:
            _, table, _, _ = GMX_EVENTS[ev]
            for c in chains:
                where = (f"chain = '{safe_ident(c)}'"
                         f" AND time >= '{_sql_dt(since)}' AND time <  '{_sql_dt(until)}'")
                log.info("force purge: %s WHERE %s", table, where)
                await ch.command(f"ALTER TABLE {table} DELETE WHERE {where}")

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    dead_pairs = set()
    try:
        for (c,ev,cs,ce) in chunks:
            if _stop:
                args["completed_chunks"] = sorted(map(list, completed_set))
                await _write_status(job_id=job_id, job_type=job_type, args=args, status="cancelled",
                                    progress=(done/total) if total else 1.0,
                                    started_at=started_at, finished_at=_utcnow())
                return
            key = (c,ev,cs.isoformat())
            if key in completed_set: continue
            pair_key = (c, ev)
            label = f"{c}/{ev}"
            if pair_key in dead_pairs:
                completed_set.add(key); done += 1; continue
            log.info("chunk %s %s..%s", label, cs, ce)
            try:
                n = await _fetch_chunk(ds, chain=c, event=ev, since=cs, until=ce)
            except Exception as exc:
                if _is_not_supported(exc):
                    log.warning("pair %s not supported — skipping: %s", label, exc)
                    dead_pairs.add(pair_key)
                    completed_set.add(key); done += 1; continue
                raise
            log.info("chunk %s rows=%d", label, n)
            completed_set.add(key); done += 1
            args["completed_chunks"] = sorted(map(list, completed_set))
            await _write_status(job_id=job_id, job_type=job_type, args=args, status="running",
                                progress=done/total, started_at=started_at)
            await asyncio.sleep(INTER_CHUNK_SLEEP_S)
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="completed",
                            progress=1.0, started_at=started_at, finished_at=_utcnow())
    except Exception as exc:
        log.exception("job %s failed", job_id)
        args["completed_chunks"] = sorted(map(list, completed_set))
        await _write_status(job_id=job_id, job_type=job_type, args=args, status="failed",
                            progress=(done/total) if total else 0.0,
                            started_at=started_at, finished_at=_utcnow(), error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m jobs.backfill_gmx_events <job_id>", file=sys.stderr); sys.exit(2)
    asyncio.run(main(sys.argv[1]))
