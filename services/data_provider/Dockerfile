FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8000

# `--single-process` is inherited from the stack-wide Sanic convention (ingestion
# / admin_server / data_server run the same CMD). For THOSE it's mandatory —
# they hold in-memory supervisor + subprocess state that multiple workers would
# duplicate. data_provider has no such requirement: it's stateless (no in-memory
# cache; `_FILTER_KEYS` is a static import-time table; the CH client is a
# per-process singleton that's fine to have one of per worker; snapshots are on a
# shared volume). The flag was never justified for this service specifically.
#
# KNOWN COST (measured on the deploy host 2026-08-23, clients reported slowdowns):
# the process pegs ~1 core and 388 of its 389 threads have accrued ZERO CPU time
# — all work is on the one event-loop thread. The hot path does its CPU work
# synchronously with no await to yield: `_parquet_response` (app.py),
# `pl.from_arrow` in `query_polars` and `writer.write_table` in
# `stream_query_to_parquet` (ch.py). So one client's parquet serialization
# head-of-line-blocks every other client. Only `snapshots.py`'s DuckDB scan
# offloads correctly, via `asyncio.to_thread`.
#
# IF YOU CHANGE THIS: `--workers=N` here fixes cross-client blocking (it does
# NOT speed up any single request), but (a) peak RAM multiplies by N — this
# service peaks ~5.5 GiB/request and the container has no mem_limit, so set one;
# and (b) `snapshots.py` save_key writes go straight to the final path with no
# tmp+rename, so two workers saving the same key could interleave (the single
# loop makes that impossible today). The better fix is wrapping the blocking
# polars/pyarrow calls in `asyncio.to_thread` — that composes with workers.
CMD ["python", "-m", "sanic", "src.app:app", "--host=0.0.0.0", "--port=8000", "--single-process"]
