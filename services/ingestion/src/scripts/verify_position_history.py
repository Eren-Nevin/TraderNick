"""Diagnostic: verify our stored position snapshots against DeFiStream.

For a given wallet and a single 15-minute snapshot boundary, fetch the
wallet's *complete* position book straight from DeFiStream (wallet-scoped —
all tokens, not just the ingested roster) and compare it row-for-row against
what we have in ClickHouse:

  - the raw  `hl_position_history` (read with FINAL), and
  - the derived `hl_position_history_15m` (argMaxMerge of the aggregate states)

per (token, side): amount (tokens), size (USD notional), unrealized_pnl.

Why this exists: our live/backfill ingests position_history token-scoped
(`token(*INGEST_TOKENS)`), so a wallet's full book in CH is only as complete
as the roster, and the derived tables roll the raw snapshots up via argMax.
This script is the ground-truth check that those two layers match the source.
The same wallet-scoped fetch could later back a "live full position book"
panel in the Smart Wallets dialog — but for now it's purely a checker.

NOTE: DeFiStream is queried with the same `min_size` our ingestion uses
(default $1000), so sub-threshold positions we intentionally drop don't show
up as false "missing in ours" rows. Pass --min-size 0 to see everything.

Run inside an ingestion container (needs DEFISTREAM_API_KEY + CH access):

    docker exec tradernick-hyperliquid_backfill-1 \\
        python -m scripts.verify_position_history \\
        --wallet 0x7d0c59691fec7f3fea385cdc3126c37a41d91b28 \\
        --at 2026-06-13T15:00:00

`--at` must be a 15-minute boundary (00/15/30/45). Defaults to the most
recently-closed boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

import config
from clickhouse import async_client

# DeFiStream API base — same default the SDK uses.
_BASE_URL = os.environ.get("DEFISTREAM_BASE_URL", "https://api.defistream.dev/v1").rstrip("/")

# Per-field relative tolerance for "match" (mark price differs slightly
# between DeFiStream's re-derivation and our stored snapshot, so unrealized
# PnL especially won't be bit-identical). Absolute floor avoids div-by-zero
# noise on near-zero values.
_REL_TOL = 0.01     # 1%
_ABS_TOL = 1.0      # $1 / 1 token


def _floor_15m(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def _parse_at(s: str | None) -> datetime:
    if not s:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return _floor_15m(now) - timedelta(minutes=15)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    dt = dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    if dt != _floor_15m(dt):
        print(f"WARNING: --at {s} is not a 15m boundary; snapping to {_floor_15m(dt)}",
              file=sys.stderr)
    return _floor_15m(dt)


def _iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── DeFiStream (ground truth) ──────────────────────────────────────────────

def _fetch_defistream(wallet: str, at: datetime, min_size: float) -> dict:
    """Return {(token, side): {amount, size, unrealized_pnl}} for the wallet's
    full book at the 15m boundary `at`."""
    if not config.DEFISTREAM_API_KEY:
        raise SystemExit("DEFISTREAM_API_KEY is not set")
    params = {
        "wallets": wallet.lower(),
        "since": _iso_z(at),
        "until": _iso_z(at + timedelta(minutes=15)),
        "window": "15m",
        "market_type": "perp",
        "format": "csv",
    }
    if min_size and min_size > 0:
        params["min_size"] = str(min_size)
    r = httpx.get(f"{_BASE_URL}/exchange/hyperliquid/position_history",
                  params=params, headers={"X-API-Key": config.DEFISTREAM_API_KEY},
                  timeout=120.0)
    r.raise_for_status()
    out: dict[tuple[str, str], dict[str, float]] = {}
    rows = csv.DictReader(io.StringIO(r.text))
    for row in rows:
        tok, side = row.get("token"), row.get("side")
        ts = row.get("time", "")
        if not tok or not side:
            continue
        # The window can echo the next boundary too; keep only `at`.
        if not ts.startswith(_iso_z(at)):
            continue
        out[(tok, side)] = {
            "amount": float(row.get("amount") or 0),
            "size": float(row.get("size") or 0),
            "unrealized_pnl": float(row.get("unrealized_pnl") or 0),
        }
    return out


# ── Our ClickHouse tables ──────────────────────────────────────────────────

async def _fetch_raw(ch, wallet: str, at: datetime) -> dict:
    """Raw hl_position_history (FINAL) at the snapshot."""
    rs = await ch.query(
        "SELECT token, side, amount, size, unrealized_pnl "
        "FROM tradernick.hl_position_history FINAL "
        "WHERE wallet = {w:String} AND time = {at:DateTime64(3)}",
        parameters={"w": wallet.lower(), "at": at},
    )
    return {(r[0], r[1]): {"amount": float(r[2]), "size": float(r[3]),
                           "unrealized_pnl": float(r[4])}
            for r in rs.result_rows}


async def _fetch_derived_15m(ch, wallet: str, at: datetime) -> dict:
    """Derived hl_position_history_15m (argMaxMerge of the states)."""
    rs = await ch.query(
        "SELECT token, side, "
        "       argMaxMerge(amount_state) AS amount, "
        "       argMaxMerge(size_state)   AS size, "
        "       argMaxMerge(pnl_state)    AS unrealized_pnl "
        "FROM tradernick.hl_position_history_15m "
        "WHERE wallet = {w:String} AND bucket = {at:DateTime} "
        "GROUP BY token, side",
        parameters={"w": wallet.lower(), "at": at},
    )
    return {(r[0], r[1]): {"amount": float(r[2]), "size": float(r[3]),
                           "unrealized_pnl": float(r[4])}
            for r in rs.result_rows}


# ── Comparison ─────────────────────────────────────────────────────────────

def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_ABS_TOL, _REL_TOL * max(abs(a), abs(b)))


def _cmp_cell(ref: float | None, got: float | None) -> str:
    if ref is None and got is None:
        return "—"
    if ref is None:
        return f"{got:,.2f} (only-ours)"
    if got is None:
        return "MISSING"
    return f"{got:,.2f}" + ("" if _close(ref, got) else f" ✗(ds={ref:,.2f})")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wallet", required=True, help="wallet address (0x…)")
    ap.add_argument("--at", default=None,
                    help="15m snapshot boundary ISO (default: last closed boundary)")
    ap.add_argument("--min-size", type=float, default=1000.0,
                    help="min position size ($) — match ingestion's filter (default 1000; 0=all)")
    args = ap.parse_args()

    at = _parse_at(args.at)
    wallet = args.wallet
    print(f"wallet={wallet}  snapshot={_iso_z(at)}Z  min_size=${args.min_size:g}\n")

    ds = _fetch_defistream(wallet, at, args.min_size)
    ch = await async_client()
    try:
        raw, d15 = await asyncio.gather(_fetch_raw(ch, wallet, at),
                                        _fetch_derived_15m(ch, wallet, at))
    finally:
        try:
            await ch.close()
        except Exception:
            pass

    keys = sorted(set(ds) | set(raw) | set(d15))
    if not keys:
        print("No positions in DeFiStream or our tables at this snapshot.")
        return

    print(f"{'token':<10} {'side':<6} {'field':<15} {'DeFiStream':>16} {'raw(FINAL)':>26} {'derived_15m':>26}")
    print("-" * 102)
    # Per-(token,side) verdict buckets.
    value_mismatch: list = []   # present in DS and ours but a field differs
    missing: list = []          # in DS, absent in our CH (likely non-roster token)
    extra: list = []            # in our CH, absent in DS (stale row?)
    for (tok, side) in keys:
        d, rw, dv = ds.get((tok, side)), raw.get((tok, side)), d15.get((tok, side))
        in_ours = rw is not None or dv is not None
        if d is None and in_ours:
            extra.append((tok, side))
        elif d is not None and not in_ours:
            missing.append((tok, side))
        elif d is not None and in_ours:
            bad = any(
                (rw and not _close(d[f], rw[f])) or (dv and not _close(d[f], dv[f]))
                for f in ("amount", "size", "unrealized_pnl")
            )
            if bad:
                value_mismatch.append((tok, side))
        for fld in ("amount", "size", "unrealized_pnl"):
            dref = d[fld] if d else None
            dref_s = f"{dref:,.2f}" if dref is not None else "—"
            print(f"{tok:<10} {side:<6} {fld:<15} {dref_s:>16} "
                  f"{_cmp_cell(dref, rw[fld] if rw else None):>26} "
                  f"{_cmp_cell(dref, dv[fld] if dv else None):>26}")
        print()

    print(f"summary ({len(keys)} position rows, tol {_REL_TOL*100:g}% / ${_ABS_TOL:g}):")
    print(f"  DeFiStream={len(ds)}  raw_CH={len(raw)}  derived_15m={len(d15)}")
    matched = len(keys) - len(value_mismatch) - len(missing) - len(extra)
    print(f"  matched (DS==raw==derived): {matched}")
    if value_mismatch:
        print(f"  ✗ VALUE MISMATCH: {len(value_mismatch)} → {value_mismatch}")
    if missing:
        print(f"  · missing in our CH (likely not in INGEST_TOKENS roster): "
              f"{[t for t, _ in missing]}")
    if extra:
        print(f"  ⚠ in our CH but not DeFiStream (stale?): {extra}")
    if not value_mismatch and not extra:
        print("  → all ingested-token positions match DeFiStream ✓")


if __name__ == "__main__":
    asyncio.run(main())
