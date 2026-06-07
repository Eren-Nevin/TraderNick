"""Provider-table gap detection.

For each provider, declare which tables to scan and how dense they
should be. The /gaps endpoint runs one CH query per spec over the
requested time window and returns days where the actual row count falls
materially below the expected baseline.

Two detection modes:

  REGULAR_CADENCE — the upstream emits at a fixed cadence (1m / 5m / 8h
  / etc.) so a per-day row count is deterministic. `expected_per_day`
  is hard-coded. A day under `threshold_ratio × expected_per_day` is a
  gap. Mostly used for the exchange feeds (Binance / HL OHLCV / OI /
  funding / LSR / book_depth) where any missing minute screams gap.

  EVENT_DRIVEN — the upstream emits irregularly (AAVE deposits, transfers,
  HL fills). Baseline is the trailing-`baseline_window_days` median
  per group. A day under `threshold_ratio × median` is a gap. Inherently
  noisier — used to surface "we got 0 transfers on ETH for a day when
  the trailing 14d median is 80k" but won't catch a thin trading day.

Group columns: each spec carries the natural dim(s) (token, chain,
(chain, kind), …) so a missing-BTC day shows up even if the rest of
the tokens were fine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import logging

from clickhouse import async_client

log = logging.getLogger("gap_detection")


class GapMode(str, Enum):
    REGULAR_CADENCE = "regular_cadence"
    EVENT_DRIVEN = "event_driven"


@dataclass(frozen=True)
class GapTableSpec:
    """One row per (provider, table). Group_cols are the dims we slice
    the gap search along. expected_per_day is the per-group cadence in
    REGULAR_CADENCE mode."""
    table: str                          # 'tradernick.binance_ohlcv_1m'
    mode: GapMode = GapMode.REGULAR_CADENCE
    time_col: str = "time"
    group_cols: tuple[str, ...] = ("token",)
    expected_per_day: int = 0           # REGULAR_CADENCE only
    threshold_ratio: float = 0.5        # gap if actual < ratio × expected
    baseline_window_days: int = 14      # EVENT_DRIVEN only
    min_baseline_rows: int = 100        # below this, the table is "quiet"
                                        # and we skip gap reporting for it
    notes: str = ""


# --------------------------------------------------------------------------
# Per-provider spec catalogue. Adding a new provider → drop an entry.
# --------------------------------------------------------------------------

# Cadence constants — comments line up with the live worker's tick freq.
_PER_DAY_1m  = 24 * 60          # 1440
_PER_DAY_5m  = 24 * 12          # 288
_PER_DAY_30m = 24 * 2           # 48
_PER_DAY_1h  = 24
_PER_DAY_8h  = 3
_PER_DAY_30s = 24 * 60 * 2      # 2880 — book_depth snapshots per token
_BOOK_DEPTH_PER_SNAPSHOT = 12   # 12 bps rows per snapshot
_PER_DAY_BOOK_DEPTH = _PER_DAY_30s * _BOOK_DEPTH_PER_SNAPSHOT


GAP_SPECS: dict[str, list[GapTableSpec]] = {
    # --------------------------- Binance ----------------------------
    # All five feeds have a deterministic cadence — gaps here are
    # always real DeFiStream / supervisor incidents.
    "binance": [
        GapTableSpec("tradernick.binance_ohlcv_1m",         expected_per_day=_PER_DAY_1m,         threshold_ratio=0.7),
        GapTableSpec("tradernick.binance_open_interest",    expected_per_day=_PER_DAY_5m,         threshold_ratio=0.7),
        GapTableSpec("tradernick.binance_long_short_ratios", expected_per_day=_PER_DAY_5m,        threshold_ratio=0.7),
        # Funding rate is sparse (3/day) — let one missed row through
        # before we flag, but 0 is always a gap.
        GapTableSpec("tradernick.binance_funding_rate",     expected_per_day=_PER_DAY_8h,         threshold_ratio=0.34),
        GapTableSpec(
            "tradernick.binance_book_depth",
            expected_per_day=_PER_DAY_BOOK_DEPTH,
            threshold_ratio=0.5,
            notes="off by default in live; only flags gaps where the token IS being polled",
        ),
        # raw_trades has variable volume per minute; use event-driven so
        # the baseline tracks the token's actual trading activity rather
        # than a synthetic constant.
        GapTableSpec(
            "tradernick.binance_raw_trades",
            mode=GapMode.EVENT_DRIVEN,
            threshold_ratio=0.2,
            min_baseline_rows=10_000,
        ),
    ],

    # ------------------------- Hyperliquid --------------------------
    "hyperliquid": [
        GapTableSpec("tradernick.hl_ohlcv_1m",      expected_per_day=_PER_DAY_1m, threshold_ratio=0.7),
        # HL funding is hourly (continuous-funding model).
        GapTableSpec("tradernick.hl_funding",       expected_per_day=_PER_DAY_1h, threshold_ratio=0.5),
        GapTableSpec("tradernick.hl_trades",        mode=GapMode.EVENT_DRIVEN, threshold_ratio=0.2),
        GapTableSpec("tradernick.hl_fills",         mode=GapMode.EVENT_DRIVEN, threshold_ratio=0.2),
        GapTableSpec("tradernick.hl_position_history", mode=GapMode.EVENT_DRIVEN, threshold_ratio=0.2),
        GapTableSpec("tradernick.hl_trade_history", mode=GapMode.EVENT_DRIVEN, threshold_ratio=0.2),
        # hl_transfers + hl_vaults are very low cadence; skip from gap detection.
    ],

    # --------------------------- Transfers --------------------------
    # Single multi-chain table — slice by (chain, kind) so a missing-BTC
    # day shows up next to a missing-ETH-ERC20 day.
    "transfers": [
        GapTableSpec(
            "tradernick.transfers",
            mode=GapMode.EVENT_DRIVEN,
            group_cols=("chain", "kind"),
            threshold_ratio=0.2,
            min_baseline_rows=1_000,
        ),
    ],

    # ----------------------------- AAVE -----------------------------
    # 17 tables across v2/v3/v4. Group by (chain) — each chain × event
    # combo gets baseline-compared independently.
    "aave": [
        # V3 — pluralised by the schema (deposits/withdrawals/...).
        GapTableSpec("tradernick.aave_deposits",       mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_withdrawals",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_borrows",        mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_repays",         mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_flashloans",     mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_liquidations",   mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2, min_baseline_rows=10),
        # V2
        GapTableSpec("tradernick.aave_v2_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v2_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v2_borrows",     mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v2_repays",      mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v2_flashloans",  mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v2_liquidations", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2, min_baseline_rows=10),
        # V4 (no flashloan — ETH-only for now)
        GapTableSpec("tradernick.aave_v4_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v4_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v4_borrows",     mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v4_repays",      mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.aave_v4_liquidations", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2, min_baseline_rows=10),
    ],

    # ---------------------------- Uniswap ---------------------------
    "uniswap": [
        # V3
        GapTableSpec("tradernick.uniswap_swaps",       mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_collects",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        # V2
        GapTableSpec("tradernick.uniswap_v2_swaps",       mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_v2_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_v2_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        # V4
        GapTableSpec("tradernick.uniswap_v4_swaps",       mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_v4_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.uniswap_v4_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
    ],

    # --------------------------- Aerodrome --------------------------
    # BASE-only — group_cols=() means there's no useful slicing dim.
    "aerodrome": [
        GapTableSpec("tradernick.aero_concentrated_swaps",       mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_concentrated_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_concentrated_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_concentrated_collects",    mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_basic_swaps",       mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_basic_deposits",    mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_basic_withdrawals", mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.aero_basic_claims",      mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
    ],

    # ----------------------------- Lido -----------------------------
    "lido": [
        # ETH-only events (mainnet stETH ⇆ ETH)
        GapTableSpec("tradernick.lido_deposits",             mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.lido_withdrawal_requests",  mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.lido_withdrawal_claims",    mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        # L2 events — grouped by chain since multiple L2s
        GapTableSpec("tradernick.lido_l2_deposits",             mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.lido_l2_withdrawal_requests",  mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
    ],

    # ---------------------------- Morpho ----------------------------
    "morpho": [
        GapTableSpec("tradernick.morpho_supplies",            mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.morpho_withdrawals",         mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.morpho_borrows",             mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.morpho_repays",              mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.morpho_supply_collaterals",  mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.morpho_withdraw_collaterals", mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2),
        GapTableSpec("tradernick.morpho_liquidations",        mode=GapMode.EVENT_DRIVEN, group_cols=("chain",), threshold_ratio=0.2, min_baseline_rows=10),
    ],

    # ----------------------------- Spark ----------------------------
    "spark": [
        GapTableSpec("tradernick.spark_deposits",     mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.spark_withdrawals",  mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.spark_borrows",      mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.spark_repays",       mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.spark_flashloans",   mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.spark_liquidations", mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2, min_baseline_rows=10),
    ],

    # ------------------------------ GMX -----------------------------
    # ARB-only — no slicing dim, scan whole table per day.
    "gmx": [
        GapTableSpec("tradernick.gmx_position_increases", mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_position_decreases", mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_liquidations",       mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2, min_baseline_rows=10),
        GapTableSpec("tradernick.gmx_swaps",              mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_deposits",           mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_withdrawals",        mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_funding",            mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_borrowing",          mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
        GapTableSpec("tradernick.gmx_fees_collected",     mode=GapMode.EVENT_DRIVEN, group_cols=(), threshold_ratio=0.2),
    ],

    # data_process tables are derived MVs — no gap concept beyond their
    # upstreams. Intentionally absent.
}


# --------------------------------------------------------------------------
# Query execution.
# --------------------------------------------------------------------------

def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def _find_gaps_regular(spec: GapTableSpec, since: datetime, until: datetime) -> list[dict]:
    """SELECT per-day row count grouped by spec.group_cols, return rows
    where count < threshold × expected_per_day."""
    threshold = max(1, int(spec.expected_per_day * spec.threshold_ratio))
    group_select = ", ".join(spec.group_cols)
    group_select_with_comma = (group_select + ", ") if group_select else ""
    group_by = ", ".join(("day", *spec.group_cols))
    sql = f"""
        SELECT toDate({spec.time_col}) AS day, {group_select_with_comma} count() AS rows
        FROM {spec.table}
        WHERE {spec.time_col} >= toDateTime('{_format_dt(since)}')
          AND {spec.time_col} <  toDateTime('{_format_dt(until)}')
        GROUP BY {group_by}
        HAVING rows < {threshold}
        ORDER BY day, {group_select if group_select else 'rows'}
        LIMIT 10000
    """
    ch = await async_client()
    rs = await ch.query(sql)
    out: list[dict] = []
    for r in rs.result_rows:
        day = r[0]
        group_vals = r[1:1 + len(spec.group_cols)]
        rows = int(r[1 + len(spec.group_cols)])
        out.append({
            "table": spec.table.split(".", 1)[-1],
            "mode": "regular_cadence",
            "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
            "group": dict(zip(spec.group_cols, [str(g) for g in group_vals])),
            "rows": rows,
            "expected": spec.expected_per_day,
            "threshold": threshold,
        })
    return out


async def _find_gaps_event_driven(spec: GapTableSpec, since: datetime, until: datetime) -> list[dict]:
    """Baseline = median per-group daily row count over the trailing
    `baseline_window_days` days BEFORE `since`. Gaps = days in [since,
    until) where actual < threshold × baseline AND baseline ≥ min_baseline_rows.

    SQL shape adapts to group_cols cardinality:
      - 0 cols → CROSS JOIN against a single-row baseline (whole-table median)
      - 1+ cols → INNER JOIN baseline ON each group column"""
    baseline_since_sql = (
        f"toDateTime('{_format_dt(since)}') - INTERVAL {spec.baseline_window_days} DAY"
    )
    since_sql = f"toDateTime('{_format_dt(since)}')"
    until_sql = f"toDateTime('{_format_dt(until)}')"

    if spec.group_cols:
        cols_csv = ", ".join(spec.group_cols)
        inner_group_by = "day, " + cols_csv
        outer_group_by = cols_csv
        join_on = " AND ".join(f"c.{c} = b.{c}" for c in spec.group_cols)
        baseline_cols_select = cols_csv + ", "
        baseline_group_select = "b." + ", b.".join(spec.group_cols) + ", "  # unused; kept for clarity
        select_group_in_current = "c." + ", c.".join(spec.group_cols)
        join_clause = f"INNER JOIN baseline b ON {join_on}"
        order_by = "c.day, " + ", ".join(f"c.{c}" for c in spec.group_cols)
    else:
        cols_csv = ""
        inner_group_by = "day"
        outer_group_by = ""              # GROUP BY () would be invalid; use SELECT without GROUP BY
        baseline_cols_select = ""
        select_group_in_current = ""
        join_clause = "CROSS JOIN baseline b"
        order_by = "c.day"

    baseline_outer = (
        f"GROUP BY {outer_group_by}\nHAVING median_rows >= {spec.min_baseline_rows}"
        if spec.group_cols
        else f"HAVING median_rows >= {spec.min_baseline_rows}"
    )

    sql = f"""
        WITH
            baseline AS (
                SELECT {baseline_cols_select}quantile(0.5)(d.rows) AS median_rows
                FROM (
                    SELECT toDate({spec.time_col}) AS day, {cols_csv + ', ' if cols_csv else ''}count() AS rows
                    FROM {spec.table}
                    WHERE {spec.time_col} >= {baseline_since_sql}
                      AND {spec.time_col} <  {since_sql}
                    GROUP BY {inner_group_by}
                ) d
                {baseline_outer}
            ),
            current AS (
                SELECT toDate({spec.time_col}) AS day, {cols_csv + ', ' if cols_csv else ''}count() AS rows
                FROM {spec.table}
                WHERE {spec.time_col} >= {since_sql}
                  AND {spec.time_col} <  {until_sql}
                GROUP BY {inner_group_by}
            )
        SELECT c.day, {select_group_in_current + ', ' if select_group_in_current else ''}c.rows, b.median_rows
        FROM current c
        {join_clause}
        WHERE c.rows < b.median_rows * {spec.threshold_ratio}
        ORDER BY {order_by}
        LIMIT 10000
    """
    ch = await async_client()
    rs = await ch.query(sql)
    out: list[dict] = []
    for r in rs.result_rows:
        day = r[0]
        # Columns: day, [group_cols...], rows, baseline_median
        group_vals = r[1:1 + len(spec.group_cols)]
        rows = int(r[1 + len(spec.group_cols)])
        baseline = float(r[2 + len(spec.group_cols)])
        out.append({
            "table": spec.table.split(".", 1)[-1],
            "mode": "event_driven",
            "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
            "group": dict(zip(spec.group_cols, [str(v) for v in group_vals])),
            "rows": rows,
            "baseline_median": baseline,
            "threshold_ratio": spec.threshold_ratio,
        })
    return out


def _clip_until_to_today(until: datetime) -> datetime:
    """Clip `until` to the start of today UTC so the in-progress current
    day is never flagged as a gap. The user is asking about historical
    gaps; today's row count is intrinsically partial.

    Callers can still pass an explicit until earlier than today — we
    only clip in the direction of MORE conservative (less data scanned)."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return min(until, today_start)


async def find_gaps(provider: str, since: datetime, until: datetime) -> dict:
    """Run gap detection for every spec under `provider` and return a
    flat list of gap rows + an errors list (per-spec failures don't
    abort the call)."""
    specs = GAP_SPECS.get(provider) or []
    if not specs:
        return {"provider": provider, "gaps": [], "errors": [],
                "note": f"no gap specs configured for provider {provider!r}"}
    effective_until = _clip_until_to_today(until)
    if effective_until <= since:
        return {"provider": provider, "since": since.isoformat(),
                "until": until.isoformat(),
                "effective_until": effective_until.isoformat(),
                "gaps": [], "errors": [],
                "note": "window collapsed to <=0 days after clipping the "
                        "in-progress current day; no completed days to check"}
    gaps: list[dict] = []
    errors: list[dict] = []
    for spec in specs:
        try:
            if spec.mode == GapMode.REGULAR_CADENCE:
                rows = await _find_gaps_regular(spec, since, effective_until)
            else:
                rows = await _find_gaps_event_driven(spec, since, effective_until)
            gaps.extend(rows)
        except Exception as exc:  # noqa: BLE001
            log.exception("gap query failed: %s", spec.table)
            errors.append({"table": spec.table, "error": str(exc)})
    return {"provider": provider, "since": since.isoformat(),
            "until": until.isoformat(),
            "effective_until": effective_until.isoformat(),
            "gaps": gaps, "errors": errors}


# Group_cols=() needs to multi-col SELECT to behave. We emit `''` for
# the empty-group case so the parsing path stays uniform. The
# event_driven SELECT therefore always returns 4 columns (day, g, rows,
# baseline) regardless of dim count.


# Tuple group hack: ClickHouse returns a tuple for multi-column GROUP
# BY expressions when wrapped under an alias. To keep things simple we
# accept either tuple or scalar in the row parser above.


# ===========================================================================
# Calendar view — per-event fill board.
#
# Powers the per-event coverage visualization on the backfill page (one
# fill board per event, GitHub-contributions style). Same underlying
# CH knowledge as GAP_SPECS above but sliced per-event instead of
# per-provider, with a richer response shape (per-day status + per-hour
# today strip + first/last data dates).
# ===========================================================================

import asyncio


@dataclass(frozen=True)
class CalendarEventSpec:
    """One entry per StreamSpec.name. The event_key the API takes is
    exactly the stream name, so the dashboard derives the per-provider
    event list from the existing STREAMS registry — no parallel
    frontend catalogue."""
    event_key: str                    # 'aave_v3.deposit'
    provider: str                     # 'aave'
    label: str                        # 'AAVE V3 Deposit'
    table: str                        # 'tradernick.aave_deposits'
    time_col: str = "time"
    filter_sql: str = ""              # '' or "kind = 'btc'"
    mode: GapMode = GapMode.EVENT_DRIVEN
    # Fraction of the per-hour baseline (computed dynamically) the
    # current hour must hit to be considered "filled". 0.7 is the tight
    # default for regular-cadence feeds (deterministic upstream), 0.2
    # is the loose default for event-driven tables.
    threshold_ratio: float = 0.2
    baseline_window_days: int = 7
    # Below this avg-rows-per-hour, treat the hour-of-day as
    # intrinsically inactive (not a gap). For sparse feeds like funding
    # (3 events/day) most hours of day have baseline=0 → inactive.
    min_baseline_per_hour: float = 0.1


def _reg_cadence(event_key, provider, label, table, *,
                 filter_sql="", threshold_ratio=0.7):
    return CalendarEventSpec(
        event_key=event_key, provider=provider, label=label, table=table,
        filter_sql=filter_sql, mode=GapMode.REGULAR_CADENCE,
        threshold_ratio=threshold_ratio,
    )


def _event_driven(event_key, provider, label, table, *,
                  filter_sql="", threshold_ratio=0.2,
                  min_baseline_per_hour=0.1):
    return CalendarEventSpec(
        event_key=event_key, provider=provider, label=label, table=table,
        filter_sql=filter_sql, mode=GapMode.EVENT_DRIVEN,
        threshold_ratio=threshold_ratio,
        min_baseline_per_hour=min_baseline_per_hour,
    )


def _aave_events(version, table_prefix, events):
    """version='v3' table_prefix='aave_' OR version='v2' table_prefix='aave_v2_' etc.
    events is a list of (event_name, table_suffix) — table is then
    `tradernick.{table_prefix}{table_suffix}`."""
    out = {}
    for ev, suffix in events:
        key = f"aave_{version}.{ev}"
        out[key] = _event_driven(
            key, "aave", f"AAVE {version.upper()} {ev.title()}",
            f"tradernick.{table_prefix}{suffix}",
            # liquidations are rare — wider min_baseline.
            min_baseline_per_hour=(0.05 if ev == "liquidation" else 0.1),
        )
    return out


def _uni_events(version, table_prefix, events):
    out = {}
    for ev, suffix in events:
        key = f"uniswap_{version}.{ev}"
        out[key] = _event_driven(
            key, "uniswap", f"Uniswap {version.upper()} {ev.title()}",
            f"tradernick.{table_prefix}{suffix}",
        )
    return out


_AAVE_PLURAL = [
    ("deposit", "deposits"), ("withdraw", "withdrawals"),
    ("borrow", "borrows"), ("repay", "repays"),
    ("flashloan", "flashloans"), ("liquidation", "liquidations"),
]
_AAVE_V4_PLURAL = [(e, s) for e, s in _AAVE_PLURAL if e != "flashloan"]

_UNI_PLURAL = [
    ("swap", "swaps"), ("deposit", "deposits"),
    ("withdraw", "withdrawals"), ("collect", "collects"),
]
_UNI_V2_PLURAL = [(e, s) for e, s in _UNI_PLURAL if e != "collect"]
_UNI_V4_PLURAL = _UNI_V2_PLURAL


# --- Catalogue -------------------------------------------------------------
# 83 entries — one per StreamSpec.name. data_process streams omitted
# (they manage MVs, not source data). Build via a chain of dict updates.

CALENDAR_EVENTS: dict[str, CalendarEventSpec] = {}

# Hyperliquid — 8 events. ohlcv + funding are regular cadence (1m bars,
# hourly funding); the rest are event-driven.
CALENDAR_EVENTS.update({
    "hyperliquid.ohlcv":            _reg_cadence("hyperliquid.ohlcv", "hyperliquid", "HL OHLCV 1m",            "tradernick.hl_ohlcv_1m"),
    "hyperliquid.funding":          _reg_cadence("hyperliquid.funding", "hyperliquid", "HL Funding",            "tradernick.hl_funding"),
    "hyperliquid.trades":           _event_driven("hyperliquid.trades", "hyperliquid", "HL Trades",             "tradernick.hl_trades"),
    "hyperliquid.fills":            _event_driven("hyperliquid.fills", "hyperliquid", "HL Fills",              "tradernick.hl_fills"),
    "hyperliquid.position_history": _event_driven("hyperliquid.position_history", "hyperliquid", "HL Position History", "tradernick.hl_position_history"),
    "hyperliquid.trade_history":    _event_driven("hyperliquid.trade_history", "hyperliquid", "HL Trade History", "tradernick.hl_trade_history"),
    "hyperliquid.transfers":        _event_driven("hyperliquid.transfers", "hyperliquid", "HL Transfers",       "tradernick.hl_transfers"),
    "hyperliquid.vaults":           _event_driven("hyperliquid.vaults", "hyperliquid", "HL Vaults",            "tradernick.hl_vaults"),
})

# Binance — 6 feeds. All but raw_trades are regular cadence (deterministic
# upstream emit). raw_trades' per-hour volume swings with market activity.
CALENDAR_EVENTS.update({
    "binance.ohlcv":              _reg_cadence("binance.ohlcv",             "binance", "Binance OHLCV 1m",          "tradernick.binance_ohlcv_1m"),
    "binance.open_interest":      _reg_cadence("binance.open_interest",     "binance", "Binance Open Interest",     "tradernick.binance_open_interest"),
    "binance.long_short_ratios":  _reg_cadence("binance.long_short_ratios", "binance", "Binance Long/Short Ratios", "tradernick.binance_long_short_ratios"),
    # Funding rate is 8h cadence — most hours of day have baseline=0 and
    # therefore become inactive, so the only hours that classify are the
    # 3 funding hours per day. That's the intended behaviour.
    "binance.funding_rate":       _reg_cadence("binance.funding_rate",      "binance", "Binance Funding Rate",      "tradernick.binance_funding_rate"),
    "binance.book_depth":         _reg_cadence("binance.book_depth",        "binance", "Binance Book Depth",        "tradernick.binance_book_depth"),
    "binance.raw_trades":         _event_driven("binance.raw_trades",       "binance", "Binance Raw Trades",        "tradernick.binance_raw_trades",
                                                min_baseline_per_hour=10),
})

# Transfers — 5 sub-feeds, all live in the single `transfers` table
# distinguished by the `kind` column.
CALENDAR_EVENTS.update({
    "transfers.btc":         _event_driven("transfers.btc",         "transfers", "BTC Transfers",         "tradernick.transfers", filter_sql="kind = 'btc'"),
    "transfers.evm_native":  _event_driven("transfers.evm_native",  "transfers", "EVM Native Transfers",  "tradernick.transfers", filter_sql="kind = 'native'"),
    "transfers.evm_erc20":   _event_driven("transfers.evm_erc20",   "transfers", "EVM ERC-20 Transfers",  "tradernick.transfers", filter_sql="kind = 'erc20'"),
    "transfers.tron_native": _event_driven("transfers.tron_native", "transfers", "Tron Native Transfers", "tradernick.transfers", filter_sql="kind = 'tron_native'"),
    "transfers.tron_trc20":  _event_driven("transfers.tron_trc20",  "transfers", "Tron TRC-20 Transfers", "tradernick.transfers", filter_sql="kind = 'trc20'"),
})

# AAVE V3 + V2 + V4 — table names match the schema's pluralisation.
CALENDAR_EVENTS.update(_aave_events("v3", "aave_",    _AAVE_PLURAL))
CALENDAR_EVENTS.update(_aave_events("v2", "aave_v2_", _AAVE_PLURAL))
CALENDAR_EVENTS.update(_aave_events("v4", "aave_v4_", _AAVE_V4_PLURAL))

# Uniswap V3 / V2 / V4
CALENDAR_EVENTS.update(_uni_events("v3", "uniswap_",    _UNI_PLURAL))
CALENDAR_EVENTS.update(_uni_events("v2", "uniswap_v2_", _UNI_V2_PLURAL))
CALENDAR_EVENTS.update(_uni_events("v4", "uniswap_v4_", _UNI_V4_PLURAL))

# Aerodrome (concentrated + basic — different table prefixes).
CALENDAR_EVENTS.update({
    "aerodrome.swaps":       _event_driven("aerodrome.swaps",       "aerodrome", "Aerodrome Swaps",       "tradernick.aero_concentrated_swaps"),
    "aerodrome.deposits":    _event_driven("aerodrome.deposits",    "aerodrome", "Aerodrome Deposits",    "tradernick.aero_concentrated_deposits"),
    "aerodrome.withdrawals": _event_driven("aerodrome.withdrawals", "aerodrome", "Aerodrome Withdrawals", "tradernick.aero_concentrated_withdrawals"),
    "aerodrome.collects":    _event_driven("aerodrome.collects",    "aerodrome", "Aerodrome Collects",    "tradernick.aero_concentrated_collects"),
    "aerodrome_basic.swaps":       _event_driven("aerodrome_basic.swaps",       "aerodrome", "Aerodrome Basic Swaps",       "tradernick.aero_basic_swaps"),
    "aerodrome_basic.deposits":    _event_driven("aerodrome_basic.deposits",    "aerodrome", "Aerodrome Basic Deposits",    "tradernick.aero_basic_deposits"),
    "aerodrome_basic.withdrawals": _event_driven("aerodrome_basic.withdrawals", "aerodrome", "Aerodrome Basic Withdrawals", "tradernick.aero_basic_withdrawals"),
    "aerodrome_basic.claims":      _event_driven("aerodrome_basic.claims",      "aerodrome", "Aerodrome Basic Claims",      "tradernick.aero_basic_claims"),
})

# Lido — 5 events. Mainnet (deposit/withdrawal_request/withdrawal_claimed)
# + L2 (l2_deposit/l2_withdrawal_request).
CALENDAR_EVENTS.update({
    "lido.deposit":               _event_driven("lido.deposit",               "lido", "Lido Deposit",               "tradernick.lido_deposits"),
    "lido.withdrawal_request":    _event_driven("lido.withdrawal_request",    "lido", "Lido Withdrawal Request",    "tradernick.lido_withdrawal_requests"),
    "lido.withdrawal_claimed":    _event_driven("lido.withdrawal_claimed",    "lido", "Lido Withdrawal Claimed",    "tradernick.lido_withdrawal_claims"),
    "lido.l2_deposit":            _event_driven("lido.l2_deposit",            "lido", "Lido L2 Deposit",            "tradernick.lido_l2_deposits"),
    "lido.l2_withdrawal_request": _event_driven("lido.l2_withdrawal_request", "lido", "Lido L2 Withdrawal Request", "tradernick.lido_l2_withdrawal_requests"),
})

# Morpho — 7 events.
CALENDAR_EVENTS.update({
    "morpho.supply":              _event_driven("morpho.supply",              "morpho", "Morpho Supply",              "tradernick.morpho_supplies"),
    "morpho.withdraw":            _event_driven("morpho.withdraw",            "morpho", "Morpho Withdraw",            "tradernick.morpho_withdrawals"),
    "morpho.borrow":              _event_driven("morpho.borrow",              "morpho", "Morpho Borrow",              "tradernick.morpho_borrows"),
    "morpho.repay":               _event_driven("morpho.repay",               "morpho", "Morpho Repay",               "tradernick.morpho_repays"),
    "morpho.supply_collateral":   _event_driven("morpho.supply_collateral",   "morpho", "Morpho Supply Collateral",   "tradernick.morpho_supply_collaterals"),
    "morpho.withdraw_collateral": _event_driven("morpho.withdraw_collateral", "morpho", "Morpho Withdraw Collateral", "tradernick.morpho_withdraw_collaterals"),
    "morpho.liquidation":         _event_driven("morpho.liquidation",         "morpho", "Morpho Liquidation",         "tradernick.morpho_liquidations", min_baseline_per_hour=0.05),
})

# Spark — 6 events.
CALENDAR_EVENTS.update({
    "spark.deposit":     _event_driven("spark.deposit",     "spark", "Spark Deposit",     "tradernick.spark_deposits"),
    "spark.withdraw":    _event_driven("spark.withdraw",    "spark", "Spark Withdraw",    "tradernick.spark_withdrawals"),
    "spark.borrow":      _event_driven("spark.borrow",      "spark", "Spark Borrow",      "tradernick.spark_borrows"),
    "spark.repay":       _event_driven("spark.repay",       "spark", "Spark Repay",       "tradernick.spark_repays"),
    "spark.flashloan":   _event_driven("spark.flashloan",   "spark", "Spark Flashloan",   "tradernick.spark_flashloans"),
    "spark.liquidation": _event_driven("spark.liquidation", "spark", "Spark Liquidation", "tradernick.spark_liquidations", min_baseline_per_hour=0.05),
})

# GMX — 9 events.
CALENDAR_EVENTS.update({
    "gmx.position_increase": _event_driven("gmx.position_increase", "gmx", "GMX Position Increase", "tradernick.gmx_position_increases"),
    "gmx.position_decrease": _event_driven("gmx.position_decrease", "gmx", "GMX Position Decrease", "tradernick.gmx_position_decreases"),
    "gmx.liquidation":       _event_driven("gmx.liquidation",       "gmx", "GMX Liquidation",       "tradernick.gmx_liquidations", min_baseline_per_hour=0.05),
    "gmx.swap":              _event_driven("gmx.swap",              "gmx", "GMX Swap",              "tradernick.gmx_swaps"),
    "gmx.deposit":           _event_driven("gmx.deposit",           "gmx", "GMX Deposit",           "tradernick.gmx_deposits"),
    "gmx.withdraw":          _event_driven("gmx.withdraw",          "gmx", "GMX Withdraw",          "tradernick.gmx_withdrawals"),
    "gmx.funding":           _event_driven("gmx.funding",           "gmx", "GMX Funding",           "tradernick.gmx_funding"),
    "gmx.borrowing":         _event_driven("gmx.borrowing",         "gmx", "GMX Borrowing",         "tradernick.gmx_borrowing"),
    "gmx.fees_collected":    _event_driven("gmx.fees_collected",    "gmx", "GMX Fees Collected",    "tradernick.gmx_fees_collected"),
})


# --- Query execution -------------------------------------------------------

async def _calendar_first_last(ch, spec: CalendarEventSpec) -> tuple[datetime | None, datetime | None]:
    where = f"WHERE {spec.filter_sql}" if spec.filter_sql else ""
    sql = f"SELECT min({spec.time_col}), max({spec.time_col}) FROM {spec.table} {where}"
    rs = await ch.query(sql)
    if not rs.result_rows:
        return None, None
    r = rs.result_rows[0]
    # CH returns the epoch sentinel for empty tables — guard.
    first, last = r[0], r[1]
    if first is None or (hasattr(first, "year") and first.year < 2000):
        first = None
    if last is None or (hasattr(last, "year") and last.year < 2000):
        last = None
    return first, last


async def _calendar_hourly_counts(ch, spec: CalendarEventSpec,
                                  since: datetime, until: datetime) -> dict[datetime, int]:
    where_extra = f"AND {spec.filter_sql}" if spec.filter_sql else ""
    sql = f"""
        SELECT toStartOfHour({spec.time_col}) AS h, count() AS rows
        FROM {spec.table}
        WHERE {spec.time_col} >= toDateTime('{_format_dt(since)}')
          AND {spec.time_col} <  toDateTime('{_format_dt(until)}')
          {where_extra}
        GROUP BY h
        ORDER BY h
    """
    rs = await ch.query(sql)
    return {r[0]: int(r[1]) for r in rs.result_rows}


async def _calendar_baseline_per_hour(ch, spec: CalendarEventSpec,
                                      now: datetime) -> dict[int, float]:
    """Per-hour-of-day MEDIAN row count over the trailing
    `baseline_window_days`. Returns {hour_of_day → median_rows}.
    24 entries (missing hours default to 0 in the lookup).

    Median (not mean) because a recent in-flight backfill leaves
    unmerged duplicate rows in the source table — ReplacingMergeTree
    dedupes only on background merges, so a hot partition can briefly
    show 2× / 5× the true row count. Mean baselines get blown out by
    those outlier days; median walks past them."""
    where_extra = f"AND {spec.filter_sql}" if spec.filter_sql else ""
    baseline_start = now - timedelta(days=spec.baseline_window_days)
    sql = f"""
        WITH per_hour AS (
            SELECT toStartOfHour({spec.time_col}) AS h, count() AS rows
            FROM {spec.table}
            WHERE {spec.time_col} >= toDateTime('{_format_dt(baseline_start)}')
              AND {spec.time_col} <  toDateTime('{_format_dt(now)}')
              {where_extra}
            GROUP BY h
        )
        SELECT toHour(h) AS hod, quantile(0.5)(rows) AS baseline
        FROM per_hour
        GROUP BY hod
        ORDER BY hod
    """
    rs = await ch.query(sql)
    return {int(r[0]): float(r[1]) for r in rs.result_rows}


def _classify_hours(spec: CalendarEventSpec,
                    hourly_counts: dict[datetime, int],
                    baseline_per_hod: dict[int, float],
                    since: datetime, until: datetime,
                    now: datetime) -> dict[datetime, str]:
    """Walk every hour in [since, until) and classify it as one of:
    - 'filled'   : active and met threshold
    - 'empty'    : active but below threshold (gap)
    - 'inactive' : baseline-too-low or not-yet-in-the-past (future)
    """
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    hour = since.replace(minute=0, second=0, microsecond=0)
    out: dict[datetime, str] = {}
    while hour < until:
        hod = hour.hour
        actual = hourly_counts.get(hour, 0)
        baseline = baseline_per_hod.get(hod, 0.0)
        if hour >= current_hour:
            out[hour] = "inactive"  # future / in-progress
        elif baseline < spec.min_baseline_per_hour:
            out[hour] = "inactive"
        elif actual >= baseline * spec.threshold_ratio:
            out[hour] = "filled"
        else:
            out[hour] = "empty"
        hour += timedelta(hours=1)
    return out


def _aggregate_days(hour_status: dict[datetime, str],
                    since: datetime, today: datetime) -> list[dict]:
    """One entry per day in [since.date(), today.date()).
    Today itself is omitted — it's rendered as the 24-hour strip instead."""
    out = []
    day = since.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < today:
        active = 0
        filled = 0
        for h in range(24):
            s = hour_status.get(day.replace(hour=h), "inactive")
            if s == "filled":
                filled += 1
                active += 1
            elif s == "empty":
                active += 1
        if active == 0:
            status = "gray"
        elif filled == active:
            status = "green"
        elif filled == 0:
            status = "gray"
        else:
            status = "red"
        out.append({
            "day": day.date().isoformat(),
            "status": status,
            "hours_active": active,
            "hours_filled": filled,
        })
        day += timedelta(days=1)
    return out


def _today_hours(hour_status: dict[datetime, str], today: datetime) -> list[dict]:
    """24 entries — green if filled, gray otherwise. No red for today
    (partial-hour states aren't actionable during a day in progress)."""
    out = []
    for h in range(24):
        s = hour_status.get(today.replace(hour=h), "inactive")
        out.append({"hour": h, "status": "green" if s == "filled" else "gray"})
    return out


async def find_calendar(event_key: str, since: datetime, until: datetime) -> dict:
    """Build the fill-board payload for one event over [since, until).

    Runs three CH queries in parallel: first/last, per-hour counts in
    the display window, and per-hour-of-day baseline over the trailing
    `baseline_window_days`. Classifies + aggregates in Python."""
    spec = CALENDAR_EVENTS.get(event_key)
    if spec is None:
        return {"event": event_key, "error": f"no calendar spec for event {event_key!r}",
                "days": [], "today_hours": []}
    now = datetime.utcnow().replace(microsecond=0)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ch = await async_client()
    first_last_task = _calendar_first_last(ch, spec)
    hourly_task = _calendar_hourly_counts(ch, spec, since, until)
    baseline_task = _calendar_baseline_per_hour(ch, spec, now)
    (first_data, last_data), hourly_counts, baseline_per_hod = await asyncio.gather(
        first_last_task, hourly_task, baseline_task,
    )
    hour_status = _classify_hours(
        spec, hourly_counts, baseline_per_hod, since, until, now,
    )
    days = _aggregate_days(hour_status, since, today)
    today_hours_payload = _today_hours(hour_status, today)
    return {
        "event": event_key,
        "provider": spec.provider,
        "label": spec.label,
        "table": spec.table.split(".", 1)[-1],
        "mode": spec.mode.value,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "today_utc": today.date().isoformat(),
        "first_data": first_data.date().isoformat() if first_data else None,
        "last_data": last_data.date().isoformat() if last_data else None,
        "days": days,
        "today_hours": today_hours_payload,
        "errors": [],
    }


def events_for_provider(provider: str) -> list[CalendarEventSpec]:
    """Used by admin_server and (optionally) the dashboard to enumerate
    the events to render fill boards for."""
    return [s for s in CALENDAR_EVENTS.values() if s.provider == provider]
