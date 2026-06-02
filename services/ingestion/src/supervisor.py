"""Subprocess supervisor — one process per ingestion stream (and a shrinking
set of legacy "group" processes during the migration).

A `stream` is the new unit, defined in streams.STREAMS — one subprocess per
event type (e.g. `hyperliquid.ohlcv`). The supervisor consults the persistent
`ingestion_event_state` table on startup to decide whether to spawn each
stream's worker, and exposes start/stop/restart from the admin API so the
running set can be changed without a container restart.

A `group` (legacy) is the older one-process-per-protocol unit still used by
modules in `streams.LEGACY_GROUPS_STILL_PROCESS_PER_PROTOCOL`. Each entry
there will be removed as its events get split into per-stream workers.

Both share the same supervise loop: spawn → wait → restart with exponential
backoff. Random startup jitter spreads the cold-start burst so DeFiStream
doesn't see all N workers slam in simultaneously."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import sys
import time
from typing import Dict

log = logging.getLogger("supervisor")

# Random delay before each subprocess's FIRST spawn, in seconds. Spreads the
# cold-start burst across ~N seconds so dozens of subprocesses don't all hit
# DeFiStream simultaneously, AND because subsequent ticks fire at
# `start_time + k * cadence`, this same offset is what staggers ticks
# afterwards. With ~80 streams on a 5m cadence and 120s of jitter, the
# steady-state load spreads across 40% of each 5-minute window — ~0.7
# ticks/sec at the busiest moment vs synchronous-firing's 80-tick spike.
# Override via the env var for fully synchronous testing.
# All jitter now lives inside each worker (see sweep.py — live_jitter_s,
# sweep_jitter_s). Supervisor just spawns immediately; the workers stagger
# themselves. Keep a tiny default to avoid spawning 80 subprocesses on the
# exact same OS tick (which makes "ps -ef" hard to read).
_STARTUP_JITTER_S = float(os.environ.get("GROUP_STARTUP_JITTER_S", "0.0"))


class WorkerStatus:
    """Common process status for both streams and legacy groups."""
    def __init__(self, name: str, module: str, kind: str):
        self.name = name
        self.module = module
        self.kind = kind  # "stream" or "group"
        self.pid: int | None = None
        self.started_at: float | None = None
        self.crash_count: int = 0
        self.last_exit_code: int | None = None
        self.running: bool = False
        # Set when an admin stop has been requested — supervise loop exits
        # instead of restarting after the subprocess dies.
        self.requested_stop: bool = False


class Supervisor:
    def __init__(self):
        self.workers: Dict[str, WorkerStatus] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._procs: Dict[str, asyncio.subprocess.Process] = {}

    # ------------------------------------------------------------------
    # Spawn — bulk startup from the streams registry + legacy group list.
    # ------------------------------------------------------------------
    async def start_from_registry(self):
        """Walk streams.STREAMS, consult the persisted enabled state, spawn
        one supervise task per enabled stream. Called once at server startup."""
        # Local imports — avoid a cycle with streams package and keep import
        # cost out of `python -m streams.xxx` workers.
        from streams import STREAMS
        import ch_status

        try:
            persisted = await ch_status.read_all_state()
        except Exception as exc:  # noqa: BLE001
            log.exception("read_all_state failed (treating all as enabled): %s", exc)
            persisted = {}

        for spec in STREAMS:
            enabled = persisted.get(spec.name)
            if enabled is None:
                enabled = spec.enabled_default
            if not enabled:
                log.info("stream %s disabled — not spawning", spec.name)
                self.workers[spec.name] = WorkerStatus(spec.name, spec.module, "stream")
                continue
            self._spawn(spec.name, spec.module, "stream")

    def _spawn(self, name: str, module: str, kind: str) -> None:
        """Internal: register a worker + kick off its supervise loop."""
        if name in self._tasks and not self._tasks[name].done():
            log.warning("worker %s already running — skipping spawn", name)
            return
        status = self.workers.get(name) or WorkerStatus(name, module, kind)
        status.requested_stop = False
        self.workers[name] = status
        self._tasks[name] = asyncio.create_task(self._supervise(name))

    async def _supervise(self, name: str):
        # Local import — avoid cycle with ch_status (which imports clickhouse).
        import ch_status

        status = self.workers[name]
        backoff = 1
        first = True
        while True:
            if status.requested_stop:
                log.info("worker %s stop requested — exiting supervise loop", name)
                return
            if first and _STARTUP_JITTER_S > 0:
                delay = random.uniform(0.0, _STARTUP_JITTER_S)
                log.info("worker %s: jittered startup in %.1fs", name, delay)
                await asyncio.sleep(delay)
            first = False
            log.info("starting %s %s (module=%s)", status.kind, name, status.module)
            # stderr piped so a startup crash (NameError, ImportError, etc.)
            # that never reaches a tick still surfaces an error in the
            # dashboard. We tee it to our own stderr so docker-compose logs
            # still show it, AND keep the last N lines for ch_status.write_crash.
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", status.module,
                stderr=asyncio.subprocess.PIPE,
            )
            status.pid = proc.pid
            status.started_at = time.time()
            status.running = True
            self._procs[name] = proc
            stderr_tail: list[str] = []

            async def _drain_stderr():
                assert proc.stderr is not None
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", errors="replace").rstrip()
                    # Tee to our stderr so the live logs are unchanged.
                    sys.stderr.write(text + "\n")
                    sys.stderr.flush()
                    stderr_tail.append(text)
                    if len(stderr_tail) > 20:
                        del stderr_tail[0]

            drain_task = asyncio.create_task(_drain_stderr())
            code = await proc.wait()
            # Make sure we've flushed everything before reading the tail.
            try:
                await asyncio.wait_for(drain_task, timeout=2.0)
            except asyncio.TimeoutError:
                drain_task.cancel()
            status.running = False
            status.last_exit_code = code
            self._procs.pop(name, None)
            # Always clear tick_in_progress on the way out. If the worker was
            # mid-fetch when SIGTERM'd or crashed, write_tick_start had set
            # the flag to 1 but write_tick never ran to set it back to 0.
            # Leaving it stuck makes the dashboard show RUNNING forever
            # against a process that no longer exists.
            try:
                await ch_status.clear_tick_in_progress(name)
            except Exception as exc:  # noqa: BLE001
                log.debug("clear_tick_in_progress(%s) failed: %s", name, exc)
            if status.requested_stop:
                log.info("worker %s exited after stop request (code=%s)", name, code)
                return
            if code == 0:
                log.warning("worker %s exited cleanly (code=0); restarting", name)
            else:
                status.crash_count += 1
                log.error("worker %s crashed (code=%s); restart in %ds", name, code, backoff)
                # Persist the crash + last stderr lines so the dashboard
                # shows what happened even when the worker never reached a
                # successful tick. Keep the message short.
                err_text = ("\n".join(stderr_tail[-10:]))[-1500:] or f"exit_code={code}"
                try:
                    await ch_status.write_crash(name, err_text)
                except Exception as exc:  # noqa: BLE001
                    log.exception("write_crash(%s) failed: %s", name, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60) if code != 0 else 1

    # ------------------------------------------------------------------
    # Admin API — flip enabled flag + start/stop/restart a worker.
    # ------------------------------------------------------------------
    async def admin_start(self, name: str) -> dict:
        """Persist enabled=True for the named stream and spawn it if not
        already running. Returns {ok, action}; 404 if the name isn't in the
        streams registry."""
        from streams import by_name
        import ch_status

        spec = by_name(name)
        if spec is None:
            return {"ok": False, "error": "unknown stream", "name": name}

        await ch_status.set_enabled(name, True)
        if name in self._tasks and not self._tasks[name].done():
            return {"ok": True, "kind": "stream", "action": "already_running"}
        self._spawn(name, spec.module, "stream")
        return {"ok": True, "kind": "stream", "action": "spawned"}

    async def admin_stop(self, name: str) -> dict:
        """Persist enabled=False; send SIGTERM to the subprocess (if any).
        The supervise loop sees requested_stop and exits without restart."""
        import ch_status

        status = self.workers.get(name)
        if status is None:
            return {"ok": False, "error": "unknown worker", "name": name}
        await ch_status.set_enabled(name, False)
        status.requested_stop = True
        proc = self._procs.get(name)
        if proc is not None and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        return {"ok": True, "kind": status.kind, "action": "stop_requested"}

    async def admin_restart(self, name: str) -> dict:
        """Convenience: stop then start. The actual restart happens via the
        supervise-loop re-spawn after admin_start; the SIGTERM exits the
        current child."""
        stop = await self.admin_stop(name)
        if not stop.get("ok"):
            return stop
        # Wait for the prior supervise task to exit; admin_start will start a
        # fresh one. Cap the wait so a wedged child doesn't block the admin
        # request indefinitely.
        prior = self._tasks.get(name)
        if prior is not None:
            try:
                await asyncio.wait_for(prior, timeout=10.0)
            except asyncio.TimeoutError:
                log.warning("restart %s: prior task didn't exit within 10s, forcing", name)
        return await self.admin_start(name)

    # ------------------------------------------------------------------
    def snapshot(self):
        """Return {name: WorkerStatus-as-dict} for every registered worker.
        Pairs with ch_status.read_all_status() (the tick heartbeats) in the
        admin endpoint to produce the full per-stream view."""
        return {
            name: {
                "module": s.module,
                "kind": s.kind,
                "pid": s.pid,
                "running": s.running,
                "started_at": s.started_at,
                "crash_count": s.crash_count,
                "last_exit_code": s.last_exit_code,
                "requested_stop": s.requested_stop,
            }
            for name, s in self.workers.items()
        }
