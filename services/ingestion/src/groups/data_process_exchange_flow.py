"""data_process_live worker: exchange_flow_minute self-heal tick.

Ported from monolith app.py:_exchange_flow_refresh_loop. The underlying
rebuild logic (staging-swap-recover) still lives in
scripts.bootstrap_wallets._refresh_exchange_flow_worker — this module
only owns the cadence + heartbeat shape so the worker shows up in the
admin overview's stream table just like a DeFiStream poller.

The push MV (mv_exchange_flow) compounds whenever the source `transfers`
table has unmerged duplicates (live retries / backfill replays). This
loop runs a TRUNCATE-via-EXCHANGE-TABLES rebuild FROM transfers FINAL on
a 15-minute cadence so any drift heals on its own. One run ≈ 30-50s.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

import ch_status
from scripts.bootstrap_wallets import _refresh_exchange_flow_worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [data_process.exchange_flow_self_heal] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 15 * 60  # match the monolith cadence


async def main(stream_name: str | None = None):
    # Stagger the first fire so a docker-compose-wide cold start doesn't
    # collide with provider services hitting CH at the same moment.
    await asyncio.sleep(60)
    log.info("data_process.exchange_flow_self_heal up, interval=%ds", REFRESH_INTERVAL_S)
    while True:
        next_fire = time.monotonic() + REFRESH_INTERVAL_S
        rows = 0
        err: str | None = None
        t0 = time.monotonic()
        if stream_name:
            await ch_status.write_tick_start(stream_name)
        try:
            result = await _refresh_exchange_flow_worker()
            rows = int(result.get("rows_after") or 0)
            if not result.get("ok"):
                # "already_running" returns ok=False but isn't an error —
                # treat it as a skipped tick.
                log.info("self-heal skipped: %s", result.get("reason"))
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:1000]
            log.exception("exchange_flow self-heal failed: %s", exc)
        if stream_name:
            await ch_status.write_tick(
                stream_name, rows, error=err, duration_s=time.monotonic() - t0,
            )
        await asyncio.sleep(max(0.0, next_fire - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
