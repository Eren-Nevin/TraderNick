import asyncio
import logging
import sys
import time
from typing import Dict

log = logging.getLogger("supervisor")

GROUP_MODULES = {
    "binance_ohlcv": "groups.binance_ohlcv",
    "binance_raw_trades": "groups.binance_raw_trades",
    "binance_open_interest": "groups.binance_open_interest",
    "binance_long_short_ratios": "groups.binance_long_short_ratios",
    "binance_funding_rate": "groups.binance_funding_rate",
    "evm_erc20_transfers": "groups.evm_erc20_transfers",
    "evm_native_transfers": "groups.evm_native_transfers",
    "btc_transfers": "groups.btc_transfers",
    "tron_native_transfers": "groups.tron_native_transfers",
    "tron_trc20_transfers": "groups.tron_trc20_transfers",
    "aave_events": "groups.aave_events",
    "uniswap_events": "groups.uniswap_events",
    "lido_events": "groups.lido_events",
    "aave_v2_events": "groups.aave_v2_events",
    "uniswap_v2_events": "groups.uniswap_v2_events",
}


class GroupStatus:
    def __init__(self, name: str, module: str):
        self.name = name
        self.module = module
        self.pid: int | None = None
        self.started_at: float | None = None
        self.crash_count: int = 0
        self.last_exit_code: int | None = None
        self.running: bool = False


class Supervisor:
    def __init__(self):
        self.groups: Dict[str, GroupStatus] = {}
        self._tasks: list[asyncio.Task] = []

    def start(self, group_names: list[str]):
        for name in group_names:
            module = GROUP_MODULES.get(name)
            if not module:
                log.error("unknown group %s", name)
                continue
            self.groups[name] = GroupStatus(name, module)
            self._tasks.append(asyncio.create_task(self._supervise(name)))

    async def _supervise(self, name: str):
        status = self.groups[name]
        backoff = 1
        while True:
            log.info("starting group %s (module=%s)", name, status.module)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", status.module,
            )
            status.pid = proc.pid
            status.started_at = time.time()
            status.running = True
            code = await proc.wait()
            status.running = False
            status.last_exit_code = code
            if code == 0:
                log.warning("group %s exited cleanly (code=0); restarting", name)
            else:
                status.crash_count += 1
                log.error("group %s crashed (code=%s); restart in %ds", name, code, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60) if code != 0 else 1

    def snapshot(self):
        return {
            name: {
                "module": s.module,
                "pid": s.pid,
                "running": s.running,
                "started_at": s.started_at,
                "crash_count": s.crash_count,
                "last_exit_code": s.last_exit_code,
            }
            for name, s in self.groups.items()
        }
