"""HL smart-wallet selector — reusable wallet-ranking module.

Any data_server endpoint that wants to operate on "today's smart HL wallets"
calls SmartSelector.build_cte() to get a CTE SQL fragment + parameters. The
emitted CTE produces a per-day array of wallet addresses (`smart_wallets.day`,
`smart_wallets.wallets`); the consumer INNER-JOINs on day and has() the
wallet column into the array.

Selection is criteria-based: any number of metric thresholds (min/max), plus
a single sort metric. Lookback is shared across all criteria — the trailing
window aggregates once, every metric a query touches reads from the same
daily aggregates.

Per-criterion scope override: each criterion can opt into 'global' (PnL/
volume/etc. summed across all HL tokens) or 'token' (filtered to the chart
token only) independently. The selector emits the source-table CTEs once
each with BOTH the global and token-scoped projections side-by-side
(daily_pnl_g + daily_pnl_t, etc.) when both scopes are referenced, so each
criterion can pick the column it needs without scanning the source twice.

Data sources, materialised on-demand based on which metrics are referenced:
  - hl_trade_history          → realized PnL, volume, trade count, Sharpe
  - hl_position_history_eod   → end-of-prior-day unrealized PnL snapshot
  - hl_fills_pnl_daily        → side-specific realized PnL (long_pnl / short_pnl)

Adding a new metric is a single registry entry: declare its key, label, the
sources it needs, and the SQL expression template (use `{s}` where the
column suffix should go). The selector wires the rest.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# Composable filters: a node may reference child filter nodes (`refs`). Guard
# against pathological nesting / fan-out from a hand-crafted `filter` param.
MAX_FILTER_DEPTH = 6
MAX_FILTER_NODES = 64

# Every per-node CTE name and parameter key is suffixed with the node's
# content hash so multiple filter nodes can coexist in one statement without
# colliding. `sel_token` is intentionally excluded — the chart token is
# request-global, so a single shared param is correct (and shrinks the set).
# Word boundaries keep `sided_pnl` from matching inside `sided_pnl_daily`,
# `trailing` inside `vol_trailing`, etc.; longest-first ordering is belt-and-
# suspenders on top of that.
_SUFFIXABLE_NAMES = sorted([
    "data_min", "target_days", "daily_per_wallet", "trailing",
    "unrealized_eod", "sided_pnl_daily", "sided_pnl",
    "funding_per_wallet_day", "funding_trailing",
    "vol_per_wallet_day", "vol_trailing",
    "oi_snapshots", "oi_per_bucket", "oi_per_day", "oi_trailing", "oi_cap_daily",
    "returns_per_wallet_day", "returns_trailing",
    "combined", "ranked", "own_wallets",
    "sel_since", "sel_until", "sel_top_n",
], key=len, reverse=True)
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(_SUFFIXABLE_NAMES)
    + r"|sel_crit_min_\d+|sel_crit_max_\d+)\b"
)


def _suffix_sql(text: str, suffix: str) -> str:
    """Append `_{suffix}` to every per-node CTE name / param placeholder."""
    return _SUFFIX_RE.sub(lambda m: m.group(0) + "_" + suffix, text)


def _suffix_params(params: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Suffix every param key except the shared, request-global `sel_token`."""
    out: dict[str, Any] = {}
    for k, v in params.items():
        out[k if k == "sel_token" else f"{k}_{suffix}"] = v
    return out


SRC_TRADE_HISTORY = "trade_history"
SRC_EOD = "eod"
SRC_SIDED = "sided"
SRC_FUNDING = "funding"
# HIP-3 sub-asset tokens carry a `<namespace>:` prefix (e.g. `xyz:FOO`).
# We exclude them from every wallet-ranking calculation so PnL / volume /
# OI from speculative sub-assets can't game the selector. The chart-
# display routes (smart_oi, oi_split, …) pin a specific token from the
# curated INGEST_TOKENS list, so they implicitly exclude HIP-3.
HIP3_EXCLUDE = "AND position(token, ':') = 0"
# Average wallet OI (size in tokens + USD notional, total/long/short) over
# the trailing lookback. Reads hl_position_history_1h (one row per hourly
# bucket per (wallet, token, side)) — every hour in the window contributes
# one sample, so the metric truly reflects the wallet's *typical* OI in
# the period, not just the most recent snapshot.
SRC_OI = "oi"
# Per-(day, wallet) fill volumes broken down by position side (long/short)
# and taker action (buy/sell). Reads hl_fills_vol_daily, populated by the
# MV of the same name. Each metric is available in tokens AND USD.
SRC_VOL = "vol"


@dataclass(frozen=True)
class MetricDef:
    """One ranking dimension. `column_sql` is a template — substitute `{s}`
    with 'g' (global) or 't' (token) per the criterion's effective scope to
    get the concrete column expression used in `combined`."""
    key: str
    label: str
    requires: frozenset[str]
    column_sql: str


# Return-Sharpe expressions. An annualized return-Sharpe: mean / population-
# stddev of DAILY RETURNS over the lookback × √365, where return[d] = that day's
# PnL / the day's average total OI in USD (return on capital deployed). Two PnL
# "kinds":
#   realized — daily realized-PnL delta only.
#   total    — realized delta + mark-to-market unrealized delta (the day-over-
#              day change in open-position PnL), i.e. the true equity return.
# Per (kind, scope, lookback) the returns_trailing CTE carries running sums —
# ret_sum_{kind} = Σreturn, ret_sumsq_{kind} = Σreturn², ret_cnt_{kind} = #days —
# so mean = ret_sum/ret_cnt and population variance = ret_sumsq/ret_cnt − mean²
# (no array needed). `{s}` expands to e.g. `g_l7` so the columns land on the
# lookback-tagged `combined` projections (ret_sum_total_g_l7, …). `{nd}` is the
# per-criterion minimum invested-days threshold (small-sample guard): below it
# the Sharpe is 0. Default 2 (the math minimum — stddev needs ≥2 points).
def _sharpe_expr(kind: str) -> str:
    mean = f"(ret_sum_{kind}_{{s}} / ret_cnt_{kind}_{{s}})"
    var = (f"(ret_sumsq_{kind}_{{s}} / ret_cnt_{kind}_{{s}} "
           f"- pow(ret_sum_{kind}_{{s}} / ret_cnt_{kind}_{{s}}, 2))")
    return (f"if(ret_cnt_{kind}_{{s}} >= {{nd}} AND {var} > 0, "
            f"({mean} / sqrt({var})) * sqrt(365), 0)")


_SHARPE_REALIZED = _sharpe_expr("realized")
_SHARPE_TOTAL = _sharpe_expr("total")

# Metrics locked to TOKEN scope. EMPTY now: global Sharpe used to be disabled
# here because its daily-return pipeline scanned hl_position_history_1h across
# ALL tokens per query (60d timed out). Global Sharpe now reads the pre-
# aggregated per-(day, wallet) OI capital base (hl_position_history_oi_wallet_
# daily) for its denominator and the trade_history wallet-daily rollup for its
# numerator, so the all-token scan is gone and global is allowed again. The
# mechanism is kept (coercion sites + _sort_scope below honour it) in case a
# future metric needs locking, but no metric is currently token-only.
_TOKEN_ONLY_METRICS: frozenset[str] = frozenset()


# Columns available in `combined` after source CTEs are materialised. Suffix
# `_g` = wallet's aggregate across all HL tokens; `_t` = aggregate filtered
# to the chart's token. Whichever (column, scope) combos no criterion needs
# are 0/null in the projection — but the CTE emitter only computes the
# combos that are actually referenced.
METRIC_REGISTRY: dict[str, MetricDef] = {
    "pnl_pct": MetricDef(
        "pnl_pct", "Realized PnL / Volume %",
        frozenset({SRC_TRADE_HISTORY}),
        "if(vol_{s} > 0, realized_pnl_{s} / vol_{s}, 0)",
    ),
    "unrealized_pnl_pct": MetricDef(
        "unrealized_pnl_pct", "Unrealized PnL / Volume %",
        # Needs vol from trade_history AND unrealized from EOD MV.
        frozenset({SRC_TRADE_HISTORY, SRC_EOD}),
        "if(vol_{s} > 0, unrealized_pnl_{s} / vol_{s}, 0)",
    ),
    "realized_pnl": MetricDef(
        "realized_pnl", "Realized PnL ($)",
        frozenset({SRC_TRADE_HISTORY}),
        "realized_pnl_{s}",
    ),
    "unrealized_pnl": MetricDef(
        "unrealized_pnl", "Unrealized PnL ($)",
        frozenset({SRC_EOD}),
        "unrealized_pnl_{s}",
    ),
    "total_pnl": MetricDef(
        "total_pnl", "Total PnL ($)",
        frozenset({SRC_TRADE_HISTORY, SRC_EOD}),
        "(realized_pnl_{s} + unrealized_pnl_{s})",
    ),
    "total_pnl_pct": MetricDef(
        "total_pnl_pct", "Total PnL %",
        frozenset({SRC_TRADE_HISTORY, SRC_EOD}),
        "if(vol_{s} > 0, (realized_pnl_{s} + unrealized_pnl_{s}) / vol_{s}, 0)",
    ),
    "volume": MetricDef(
        "volume", "Volume ($)",
        frozenset({SRC_TRADE_HISTORY}),
        "vol_{s}",
    ),
    "trade_count": MetricDef(
        "trade_count", "Trade count",
        frozenset({SRC_TRADE_HISTORY}),
        "trade_count_{s}",
    ),
    "long_pnl": MetricDef(
        "long_pnl", "Long PnL ($)",
        frozenset({SRC_SIDED}),
        "long_pnl_{s}",
    ),
    "short_pnl": MetricDef(
        "short_pnl", "Short PnL ($)",
        frozenset({SRC_SIDED}),
        "short_pnl_{s}",
    ),
    # Return-Sharpe (annualized). `sharpe` uses TOTAL PnL (realized + mark-to-
    # market unrealized delta); `sharpe_realized` uses realized PnL only. Both
    # divide daily PnL by that day's avg total OI ($) and need trade_history +
    # OI; the `total` kind additionally reads EOD unrealized. See _sharpe_expr.
    "sharpe": MetricDef(
        "sharpe", "Sharpe (total)",
        frozenset({SRC_TRADE_HISTORY, SRC_OI}),
        _SHARPE_TOTAL,
    ),
    "sharpe_realized": MetricDef(
        "sharpe_realized", "Sharpe (realized)",
        frozenset({SRC_TRADE_HISTORY, SRC_OI}),
        _SHARPE_REALIZED,
    ),
    # ── Average OI over the lookback (token + USD) ───────────────────
    # Source columns (per-scope) emitted by the SRC_OI trailing CTE:
    #   avg_total_oi_token_{s}, avg_long_oi_token_{s}, avg_short_oi_token_{s}
    #   avg_total_oi_usd_{s},   avg_long_oi_usd_{s},   avg_short_oi_usd_{s}
    "avg_total_oi_token": MetricDef(
        "avg_total_oi_token", "Avg Total OI (token)",
        frozenset({SRC_OI}), "avg_total_oi_token_{s}",
    ),
    "avg_long_oi_token": MetricDef(
        "avg_long_oi_token", "Avg Long OI (token)",
        frozenset({SRC_OI}), "avg_long_oi_token_{s}",
    ),
    "avg_short_oi_token": MetricDef(
        "avg_short_oi_token", "Avg Short OI (token)",
        frozenset({SRC_OI}), "avg_short_oi_token_{s}",
    ),
    "avg_total_oi_usd": MetricDef(
        "avg_total_oi_usd", "Avg Total OI ($)",
        frozenset({SRC_OI}), "avg_total_oi_usd_{s}",
    ),
    "avg_long_oi_usd": MetricDef(
        "avg_long_oi_usd", "Avg Long OI ($)",
        frozenset({SRC_OI}), "avg_long_oi_usd_{s}",
    ),
    "avg_short_oi_usd": MetricDef(
        "avg_short_oi_usd", "Avg Short OI ($)",
        frozenset({SRC_OI}), "avg_short_oi_usd_{s}",
    ),
    # Per-snapshot RoE in %, averaged over the lookback. At each hourly
    # bucket: 100 × wallet_total_unrealized_pnl_usd / wallet_total_oi_usd
    # (0 when the wallet has no OI). Then avg across all buckets in the
    # window. USD-only — the user wants $-pnl / $-OI explicitly. Source
    # columns emitted by the oi_trailing CTE: avg_roe_pct_{s}.
    "avg_roe_pct": MetricDef(
        "avg_roe_pct", "Avg RoE (%)",
        frozenset({SRC_OI}), "avg_roe_pct_{s}",
    ),
    # Average number of distinct open positions (coins held) per hourly
    # snapshot, averaged over the lookback — a basket-size / concentration
    # measure. Directional wallets hold few coins (low count); basket
    # buyers/sellers hold many. Set `max` to filter out basket traders and
    # focus on directed users; `min` to require a minimum breadth. Global
    # scope counts coins across all HL tokens; token scope degenerates to
    # "fraction of buckets holding the chart token" (0/1 averaged). Source
    # column emitted by the oi_trailing CTE: avg_n_positions_{s}.
    "avg_position_count": MetricDef(
        "avg_position_count", "Avg Position Count",
        frozenset({SRC_OI}), "avg_n_positions_{s}",
    ),
    # ── Latest-snapshot (point-in-time) variants of the OI/position
    # metrics. Instead of averaging every hourly bucket in the lookback,
    # these read the wallet's MOST RECENT snapshot within the window
    # (argMax by bucket). Use them to filter on where a wallet *currently*
    # sits (e.g. total OI $ right now) rather than its typical level over
    # the period. The lookback still bounds recency: a wallet with no
    # snapshot in the window resolves to 0. Source columns emitted by the
    # oi_trailing CTE: last_total_oi_usd_{s} / last_total_oi_token_{s} /
    # last_n_positions_{s}.
    "last_total_oi_usd": MetricDef(
        "last_total_oi_usd", "Latest Total OI ($)",
        frozenset({SRC_OI}), "last_total_oi_usd_{s}",
    ),
    "last_total_oi_token": MetricDef(
        "last_total_oi_token", "Latest Total OI (token)",
        frozenset({SRC_OI}), "last_total_oi_token_{s}",
    ),
    "last_position_count": MetricDef(
        "last_position_count", "Latest Position Count",
        frozenset({SRC_OI}), "last_n_positions_{s}",
    ),
    # ── Sided + taker volume (token + USD) ───────────────────────────
    # Source columns emitted by the SRC_VOL trailing CTE:
    #   vol_token_{s}, vol_usd_{s} (totals — vol_usd matches the legacy
    #   `volume` metric, but vol_usd_{s} is the one to read for new code)
    #   long_vol_token_{s},  long_vol_usd_{s}
    #   short_vol_token_{s}, short_vol_usd_{s}
    #   taker_buy_vol_token_{s},  taker_buy_vol_usd_{s}
    #   taker_sell_vol_token_{s}, taker_sell_vol_usd_{s}
    "volume_token": MetricDef(
        "volume_token", "Volume (token)",
        frozenset({SRC_VOL}), "vol_token_{s}",
    ),
    "long_volume_usd": MetricDef(
        "long_volume_usd", "Long Volume ($)",
        frozenset({SRC_VOL}), "long_vol_usd_{s}",
    ),
    "long_volume_token": MetricDef(
        "long_volume_token", "Long Volume (token)",
        frozenset({SRC_VOL}), "long_vol_token_{s}",
    ),
    "short_volume_usd": MetricDef(
        "short_volume_usd", "Short Volume ($)",
        frozenset({SRC_VOL}), "short_vol_usd_{s}",
    ),
    "short_volume_token": MetricDef(
        "short_volume_token", "Short Volume (token)",
        frozenset({SRC_VOL}), "short_vol_token_{s}",
    ),
    "taker_buy_volume_usd": MetricDef(
        "taker_buy_volume_usd", "Taker Buy Volume ($)",
        frozenset({SRC_VOL}), "taker_buy_vol_usd_{s}",
    ),
    "taker_buy_volume_token": MetricDef(
        "taker_buy_volume_token", "Taker Buy Volume (token)",
        frozenset({SRC_VOL}), "taker_buy_vol_token_{s}",
    ),
    "taker_sell_volume_usd": MetricDef(
        "taker_sell_volume_usd", "Taker Sell Volume ($)",
        frozenset({SRC_VOL}), "taker_sell_vol_usd_{s}",
    ),
    "taker_sell_volume_token": MetricDef(
        "taker_sell_volume_token", "Taker Sell Volume (token)",
        frozenset({SRC_VOL}), "taker_sell_vol_token_{s}",
    ),
    # ── Funding-decomposed PnL ───────────────────────────────────────
    # Separates realized PnL into directional (price-move) component
    # and funding-carry component so cash-and-carry / delta-neutral
    # wallets don't get ranked as "smart traders". Source columns from
    # the funding trailing CTE: funding_pnl_{s}.
    #
    #   non_funding_pnl   = realized_pnl − funding_pnl   (the real edge)
    #   funding_pnl_share = funding_pnl / realized_pnl   (carry-ness)
    #
    # Carry wallets cluster non_funding_pnl near 0 and funding_pnl_share
    # near 1; directional traders cluster non_funding_pnl ≫ 0 and
    # funding_pnl_share ≪ 1. Combine both as criteria to be strict:
    #   non_funding_pnl ≥ X AND funding_pnl_share ≤ 0.5.
    "non_funding_pnl": MetricDef(
        "non_funding_pnl", "Non-Funding PnL ($)",
        frozenset({SRC_TRADE_HISTORY, SRC_FUNDING}),
        "(realized_pnl_{s} - funding_pnl_{s})",
    ),
    # Share is undefined when realized_pnl ≤ 0; guard with `if` so
    # those wallets don't blow the divide. Value > 0 makes the
    # filter "funding_pnl_share ≤ 0.5" semantically clean.
    "funding_pnl_share": MetricDef(
        "funding_pnl_share", "Funding PnL Share",
        frozenset({SRC_TRADE_HISTORY, SRC_FUNDING}),
        "if(realized_pnl_{s} > 0, funding_pnl_{s} / realized_pnl_{s}, 0)",
    ),
}


@dataclass
class SmartCriterion:
    metric: str
    min: float | None = None
    max: float | None = None
    # None means "inherit from the overall selector scope". Setting 'global'
    # or 'token' here overrides per-criterion — lets the user mix scopes
    # (e.g. "global PnL ≥ 50K AND token-specific volume ≥ 1M").
    scope: str | None = None
    # None means "inherit the node's lookback". A per-criterion lookback lets
    # the user window each metric independently, e.g. "volume ≥ 100K over 10d
    # AND realized PnL ≥ 10K over 3d". The trailing CTEs compute every
    # referenced lookback in one pass via conditional aggregation.
    lookback: int | None = None
    # Minimum invested-days threshold — only meaningful for the Sharpe metrics
    # (a small-sample guard; below it the Sharpe is 0). None → default 2 (no
    # extra filtering). Configurable per criterion alongside `lookback`.
    min_days: int | None = None
    # Soft-disable: the criterion stays in the list but its min/max bounds
    # don't filter and its source CTE isn't materialised (unless something
    # else references it). Lets the user A/B different criteria without
    # losing the saved values. Sort scope still honours a disabled
    # criterion's scope field if it's the sort metric.
    disabled: bool = False


@dataclass
class SmartSelector:
    lookback_days: int
    top_n: int
    scope: Literal["global", "token"]
    sort_by: str
    criteria: list[SmartCriterion] = field(default_factory=list)
    token: str | None = None
    # Child filter nodes AND-ed (per-day set-intersected) into this node's own
    # ranked result. A node with `criteria == []` contributes no own ranking
    # and is purely the intersection of its refs. See build_cte / _emit.
    refs: list["SmartSelector"] = field(default_factory=list)

    # ── parsing / validation ────────────────────────────────────────────

    @classmethod
    def from_json(cls, raw: str | None, token: str | None) -> "SmartSelector":
        if not raw:
            raise ValueError("missing selector param")
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"selector is not valid JSON: {e}")
        if not isinstance(obj, dict):
            raise ValueError("selector must be a JSON object")
        return cls._from_obj(obj, token, depth=0, counter=[0])

    @classmethod
    def _from_obj(cls, obj: Any, token: str | None,
                  depth: int, counter: list[int]) -> "SmartSelector":
        """Parse one (possibly nested) filter node. `counter` is a shared
        single-element list used as a mutable node count across the tree."""
        if not isinstance(obj, dict):
            raise ValueError("selector node must be a JSON object")
        if depth > MAX_FILTER_DEPTH:
            raise ValueError(f"selector nesting exceeds max depth {MAX_FILTER_DEPTH}")
        counter[0] += 1
        if counter[0] > MAX_FILTER_NODES:
            raise ValueError(f"selector exceeds max node count {MAX_FILTER_NODES}")

        raw_criteria = obj.get("criteria", [])
        if not isinstance(raw_criteria, list):
            raise ValueError("selector.criteria must be a JSON array")
        raw_refs = obj.get("refs", [])
        if not isinstance(raw_refs, list):
            raise ValueError("selector.refs must be a JSON array")
        has_criteria = len(raw_criteria) > 0
        if not has_criteria and not raw_refs:
            raise ValueError("selector node must have at least one criterion or ref")

        # lookback / top_n / sort_by govern this node's OWN ranking. They are
        # only meaningful when the node has criteria; a pure-composite node
        # (refs only) ignores them, so we accept defaults there rather than
        # forcing the client to send dummy values.
        lookback = obj.get("lookback")
        if not isinstance(lookback, int) or not (1 <= lookback <= 180):
            if has_criteria:
                raise ValueError("selector.lookback must be int in [1, 180]")
            lookback = 1
        top_n = obj.get("top_n")
        if not isinstance(top_n, int) or not (1 <= top_n <= 500):
            if has_criteria:
                raise ValueError("selector.top_n must be int in [1, 500]")
            top_n = 1
        scope = obj.get("scope", "global")
        if scope not in ("global", "token"):
            raise ValueError("selector.scope must be 'global' or 'token'")
        # Legacy alias: `sharpe_annualized` was the annualized realized Sharpe,
        # now named `sharpe_realized`. Migrate old persisted wires so they keep
        # their (realized) meaning and don't fail validation. (`sharpe` now
        # means the TOTAL-PnL Sharpe — not migrated.)
        sort_by = obj.get("sort_by")
        if sort_by == "sharpe_annualized":
            sort_by = "sharpe_realized"
        if has_criteria:
            if not isinstance(sort_by, str) or sort_by not in METRIC_REGISTRY:
                raise ValueError(f"selector.sort_by must be one of: {list(METRIC_REGISTRY)}")
        elif not (isinstance(sort_by, str) and sort_by in METRIC_REGISTRY):
            sort_by = "realized_pnl"  # unused; keep the dataclass field valid

        criteria: list[SmartCriterion] = []
        for i, c in enumerate(raw_criteria):
            if not isinstance(c, dict):
                raise ValueError(f"selector.criteria[{i}] must be an object")
            metric = c.get("metric")
            if metric == "sharpe_annualized":  # legacy alias → realized Sharpe
                metric = "sharpe_realized"
            if metric not in METRIC_REGISTRY:
                raise ValueError(f"selector.criteria[{i}].metric unknown: {metric}")
            cmin = c.get("min")
            cmax = c.get("max")
            if cmin is not None and not isinstance(cmin, (int, float)):
                raise ValueError(f"selector.criteria[{i}].min must be number or null")
            if cmax is not None and not isinstance(cmax, (int, float)):
                raise ValueError(f"selector.criteria[{i}].max must be number or null")
            cscope = c.get("scope")
            if cscope is not None and cscope not in ("global", "token"):
                raise ValueError(f"selector.criteria[{i}].scope must be 'global', 'token', or null")
            # Sharpe metrics are token-only for now — coerce any global/inherit
            # to token so a stale or hand-crafted global Sharpe can't time out.
            if metric in _TOKEN_ONLY_METRICS:
                cscope = "token"
            clookback = c.get("lookback")
            if clookback is not None and (not isinstance(clookback, int) or not (1 <= clookback <= 180)):
                raise ValueError(f"selector.criteria[{i}].lookback must be int in [1, 180] or null")
            cmin_days = c.get("min_days")
            if cmin_days is not None and (not isinstance(cmin_days, int) or not (2 <= cmin_days <= 180)):
                raise ValueError(f"selector.criteria[{i}].min_days must be int in [2, 180] or null")
            cdisabled = c.get("disabled", False)
            if not isinstance(cdisabled, bool):
                raise ValueError(f"selector.criteria[{i}].disabled must be a boolean")
            criteria.append(SmartCriterion(
                metric=metric,
                min=float(cmin) if cmin is not None else None,
                max=float(cmax) if cmax is not None else None,
                scope=cscope,
                lookback=clookback,
                min_days=cmin_days,
                disabled=cdisabled,
            ))

        refs = [cls._from_obj(r, token, depth + 1, counter) for r in raw_refs]

        return cls(
            lookback_days=lookback,
            top_n=top_n,
            scope=scope,
            sort_by=sort_by,
            criteria=criteria,
            token=token,
            refs=refs,
        )

    # ── content hashing (per-node dedup + cache key) ─────────────────────

    def _own_canonical(self) -> dict[str, Any]:
        """Canonical form of this node's OWN ranking config (excludes refs).
        Mirrors the FE smartSelectorCacheKey so the two layers agree."""
        return {
            "lookback": self.lookback_days,
            "top_n": self.top_n,
            "scope": self.scope,
            "sort_by": self.sort_by,
            "criteria": [
                {"metric": c.metric, "min": c.min, "max": c.max,
                 "scope": c.scope, "lookback": c.lookback,
                 "min_days": c.min_days, "disabled": c.disabled}
                for c in self.criteria
            ],
        }

    def _full_canonical(self) -> dict[str, Any]:
        """Own config plus refs (children sorted so order doesn't matter)."""
        d = self._own_canonical()
        d["refs"] = sorted(
            (c._full_canonical() for c in self.refs),
            key=lambda x: json.dumps(x, sort_keys=True),
        )
        return d

    @staticmethod
    def _suffix_of(canonical: dict[str, Any]) -> str:
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return "n" + hashlib.sha1(raw.encode()).hexdigest()[:10]

    def _own_suffix(self) -> str:
        return self._suffix_of(self._own_canonical())

    def _full_suffix(self) -> str:
        return self._suffix_of(self._full_canonical())

    def cache_key(self) -> str:
        """Full (collision-safe) content hash of this filter, used as the
        wallet-set cache key. Window-independent on purpose — a day's wallet
        set depends only on the filter + that day, not the chart's range."""
        raw = json.dumps(self._full_canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode()).hexdigest()

    def uses_token_scope(self) -> bool:
        """True if any node (this one or a ref) resolves a metric in token
        scope — i.e. the wallet set genuinely depends on the chart token."""
        if any(self._effective_scope(c.scope) == "token"
               for c in self.criteria if not c.disabled):
            return True
        if not self.criteria and self.scope == "token":
            return True
        return any(r.uses_token_scope() for r in self.refs)

    # ── scope resolution ────────────────────────────────────────────────

    def _effective_scope(self, criterion_scope: str | None) -> str:
        return criterion_scope or self.scope

    def _effective_lookback(self, criterion_lookback: int | None) -> int:
        return criterion_lookback or self.lookback_days

    def _sort_scope(self) -> str:
        """Scope to use when ordering by `sort_by`. If sort_by matches a
        criterion in the list, that criterion's effective scope wins.
        Otherwise the overall scope is the fallback — keeps orphaned
        sort metrics from breaking when they're not in the criteria UI."""
        # Token-only metrics (Sharpe) ignore the node scope entirely — they
        # always rank in token scope even when sort_by isn't a listed criterion.
        if self.sort_by in _TOKEN_ONLY_METRICS:
            return "token"
        for c in self.criteria:
            if c.metric == self.sort_by:
                return self._effective_scope(c.scope)
        return self.scope

    def _sort_lookback(self) -> int:
        """Lookback for the sort metric — the matching criterion's effective
        lookback if present, else the node default."""
        for c in self.criteria:
            if c.metric == self.sort_by:
                return self._effective_lookback(c.lookback)
        return self.lookback_days

    @staticmethod
    def _effective_min_days(criterion_min_days: int | None) -> int:
        """Min invested-days threshold for a criterion — its own value, else 2
        (the math minimum, i.e. the prior no-guard behaviour)."""
        return max(int(criterion_min_days), 2) if criterion_min_days else 2

    def _sort_min_days(self) -> int:
        """min_days for the sort metric — the matching criterion's value if the
        sort metric is itself a criterion, else the default (2)."""
        for c in self.criteria:
            if c.metric == self.sort_by:
                return self._effective_min_days(c.min_days)
        return 2

    def _needs(self) -> set[tuple[str, str, int]]:
        """Set of (source, scope, lookback) every active metric references.
        Disabled criteria don't pull their source in (the ranked CTE skips
        their WHERE clause too); the sort metric always counts regardless of
        any criterion's disabled state."""
        needs: set[tuple[str, str, int]] = set()
        ss, sl = self._sort_scope(), self._sort_lookback()
        for src in METRIC_REGISTRY[self.sort_by].requires:
            needs.add((src, ss, sl))
        for c in self.criteria:
            if c.disabled:
                continue
            eff = self._effective_scope(c.scope)
            lb = self._effective_lookback(c.lookback)
            for src in METRIC_REGISTRY[c.metric].requires:
                needs.add((src, eff, lb))
        return needs

    # Sharpe metrics → the daily-return kind their running sums use.
    _SHARPE_KIND = {"sharpe": "total", "sharpe_realized": "realized"}

    def _sharpe_combos(self) -> set[tuple[str, str, int]]:
        """(kind, scope-letter, lookback) combos where a Sharpe metric is
        referenced (sort or an active criterion). Drives emission of the returns
        CTEs + the per-combo running-sum columns the Sharpe expressions read."""
        out: set[tuple[str, str, int]] = set()
        if self.sort_by in self._SHARPE_KIND:
            out.add((self._SHARPE_KIND[self.sort_by],
                     self._suffix(self._sort_scope()), self._sort_lookback()))
        for c in self.criteria:
            if c.disabled or c.metric not in self._SHARPE_KIND:
                continue
            out.add((self._SHARPE_KIND[c.metric],
                     self._suffix(self._effective_scope(c.scope)),
                     self._effective_lookback(c.lookback)))
        return out

    def _oi_global_nonsharpe(self) -> bool:
        """True when a NON-Sharpe metric needs global-scope OI. That's the only
        thing that justifies building the expensive all-token oi_per_bucket /
        oi_per_day GLOBAL projections — Sharpe's global capital base comes from
        the cheap hl_position_history_oi_wallet_daily rollup instead. When this
        is False and only Sharpe wants global OI, the global OI build is skipped
        entirely (see _build_node_ctes)."""
        def wants(metric: str, scope: str) -> bool:
            if metric in self._SHARPE_KIND:
                return False
            return (SRC_OI in METRIC_REGISTRY[metric].requires
                    and self._suffix(scope) == "g")
        if wants(self.sort_by, self._sort_scope()):
            return True
        for c in self.criteria:
            if c.disabled:
                continue
            if wants(c.metric, self._effective_scope(c.scope)):
                return True
        return False

    @staticmethod
    def _suffix(scope: str) -> str:
        return "g" if scope == "global" else "t"

    @classmethod
    def _metric_expr(cls, metric_key: str, scope: str, lookback: int,
                     min_days: int = 2) -> str:
        """Concrete column expression for a metric at a (scope, lookback).
        `{s}` in the registry template expands to e.g. `g_l3` so it lands on
        the lookback-tagged `combined` columns (`realized_pnl_g_l3`, …). `{nd}`
        (Sharpe templates only) expands to the min invested-days threshold; it
        is a no-op for metrics whose template doesn't reference it."""
        return (METRIC_REGISTRY[metric_key].column_sql
                .replace("{s}", f"{cls._suffix(scope)}_l{lookback}")
                .replace("{nd}", str(max(int(min_days), 2))))

    # ── CTE emission ────────────────────────────────────────────────────

    def _build_node_ctes(
        self, since_dt: datetime, until_dt: datetime
    ) -> tuple[list[str], dict[str, Any]]:
        """Emit this node's OWN ranking CTE chain (unsuffixed). Returns
        (ctes, params); the final CTE is `own_wallets(day, wallets[])`. The
        driver `build_cte` suffixes names/params per node and composes the
        intersection with child refs. Only called when the node has criteria."""
        needs = self._needs()
        # Per-source: the union of scopes referenced (drives the scope-only
        # daily CTEs via proj_pair) and the set of (scope-letter, lookback)
        # combos (drives the lookback-windowed trailing columns).
        scopes_for: dict[str, set[str]] = {
            SRC_TRADE_HISTORY: set(),
            SRC_EOD: set(),
            SRC_SIDED: set(),
            SRC_OI: set(),
            SRC_VOL: set(),
            SRC_FUNDING: set(),
        }
        combos_for: dict[str, set[tuple[str, int]]] = {k: set() for k in scopes_for}
        for (src, sc, lb) in needs:
            scopes_for[src].add(sc)
            combos_for[src].add((self._suffix(sc), lb))
        any_source = any(combos_for[s] for s in scopes_for)
        if not any_source:
            raise ValueError(
                "selector references no sources — at least one metric "
                "(criterion or sort) must be defined")

        # Sharpe's GLOBAL capital base now comes from the cheap
        # hl_position_history_oi_wallet_daily rollup (oi_cap_daily CTE below),
        # not the all-token oi_per_bucket scan. So when global OI is referenced
        # ONLY by Sharpe (no global OI metric like avg_total_oi_usd), drop the
        # global OI scope/combo here — the expensive global oi_per_bucket build
        # is skipped and the SRC_OI block emits only what real OI metrics need
        # (token scope, if any). Token-scope Sharpe keeps using oi_per_day.
        sharpe_combos_early = self._sharpe_combos()
        sharpe_global = any(s == "g" for (_, s, _) in sharpe_combos_early)
        if sharpe_global and not self._oi_global_nonsharpe():
            scopes_for[SRC_OI].discard("global")
            combos_for[SRC_OI] = {(s, lb) for (s, lb) in combos_for[SRC_OI]
                                  if s != "g"}

        def src_max_lb(src: str) -> int:
            return max((lb for (_, lb) in combos_for[src]), default=1)
        # The day-spine gate uses the longest referenced lookback: a chart day
        # is only valid when even the widest window has data behind it.
        gate_lb = max((lb for (_, _, lb) in needs), default=1)

        VOL_COLS = [
            "vol_token", "vol_usd",
            "long_vol_token", "long_vol_usd",
            "short_vol_token", "short_vol_usd",
            "taker_buy_vol_token", "taker_buy_vol_usd",
            "taker_sell_vol_token", "taker_sell_vol_usd",
        ]

        # SQL fragment builders ─────────────────────────────────────────
        def proj_pair(global_expr: str, token_expr: str, src: str,
                      g_alias: str, t_alias: str) -> str:
            """Emit just the scope projections that are referenced for src."""
            parts: list[str] = []
            if "global" in scopes_for[src]:
                parts.append(f"{global_expr} AS {g_alias}")
            if "token" in scopes_for[src]:
                parts.append(f"{token_expr} AS {t_alias}")
            return ",\n                   ".join(parts)

        ctes: list[str] = []

        # data_min — earliest time available in whichever source we're
        # using for the gate. trade_history is the most common; otherwise
        # eod / sided / oi / vol. The gate's purpose is to drop chart days
        # whose trailing window starts before any data exists.
        if scopes_for[SRC_TRADE_HISTORY]:
            gate_src, gate_time_col = "tradernick.hl_trade_history", "time"
        elif scopes_for[SRC_EOD]:
            gate_src, gate_time_col = "tradernick.hl_position_history_eod_wallet", "day"
        elif scopes_for[SRC_SIDED]:
            gate_src, gate_time_col = "tradernick.hl_fills_pnl_daily", "day"
        elif scopes_for[SRC_VOL]:
            gate_src, gate_time_col = "tradernick.hl_fills_vol_daily", "day"
        elif scopes_for[SRC_FUNDING]:
            gate_src, gate_time_col = "tradernick.hl_funding_daily", "day"
        else:
            gate_src, gate_time_col = "tradernick.hl_position_history_1h", "bucket"
        ctes.append(
            f"data_min AS (\n"
            f"            SELECT toDate(min({gate_time_col})) AS min_d\n"
            f"            FROM {gate_src}\n"
            f"        )"
        )
        ctes.append(
            "target_days AS (\n"
            "            SELECT d_set.d AS d\n"
            "            FROM (\n"
            "                SELECT toDate({sel_since:DateTime}) + number AS d\n"
            "                FROM numbers(0, dateDiff('day', toDate({sel_since:DateTime}), toDate({sel_until:DateTime})) + 1)\n"
            "            ) d_set\n"
            "            CROSS JOIN data_min\n"
            "            WHERE d_set.d - " + str(gate_lb) + " >= data_min.min_d\n"
            "        )"
        )

        # ── trade_history ───────────────────────────────────────────
        if combos_for[SRC_TRADE_HISTORY]:
            th_max = src_max_lb(SRC_TRADE_HISTORY)
            # trade_history snapshots are now DAILY + ABSOLUTE (cumulative from
            # the wallet's inception), so they can't be summed. We convert them
            # to per-day deltas: a per-(wallet, day, scope) cumulative, then the
            # daily delta cum[d] − cum[d−1] via lagInFrame. Summing those deltas
            # downstream (trailing / Sharpe) telescopes to the correct endpoint
            # difference cum[d] − cum[d−L−1], so the sum() logic is unchanged.
            # The fetch reaches th_max+1 days back so the earliest in-window day
            # (target.d − th_max) has a prior snapshot to diff against; that
            # pre-roll day's own delta is outside every trailing window and never
            # contributes.
            #
            # The per-(d,wallet) cumulative is built two different ways by scope,
            # because the dedup strategy that's cheap for one is catastrophic for
            # the other:
            #
            #   TOKEN-only: prefilter to the chart token, dedup the RMT with
            #   `argMax(metric, (time, ingested_at))` per (d, wallet, token), then
            #   sum. NO FINAL — argMax picks the byte-identical winning row FINAL
            #   would, and dropping FINAL lets the optimizer keep the
            #   `(token, time, wallet)` projection so the scan prunes to one
            #   token's rows (~10×). Cardinality is tiny (one token's wallets).
            #
            #   GLOBAL (or mixed): no token prefilter is possible, so that
            #   per-(d,wallet,token) argMax explodes to ≈all wallets × all tokens
            #   × every day (~200M groups → >100 GiB, OOMs the AggregatingTransform).
            #   The data is exactly one snapshot per (wallet, token, day)
            #   (verified across the window), so FINAL dedups the RMT and we sum
            #   the per-token snapshots straight into per-(d,wallet) cumulatives
            #   in a SINGLE GROUP BY (~45M groups / 75d, ~12 GiB, ~7s). FINAL
            #   streams; there's no projection prune to lose here since global
            #   reads every token regardless.
            is_global_th = "global" in scopes_for[SRC_TRADE_HISTORY]
            # Pure-global (no token TH metric in this node) → read the
            # hl_trade_history_wallet_daily rollup, which already summed the
            # token dimension away (HIP3 excluded at build) into one row per
            # (day, wallet). This skips the 251M-row source FINAL scan + per-
            # query token collapse that made global ranking slow/OOM. Mixed
            # global+token in one node can't be served by the token-less rollup,
            # so it falls back to the source FINAL scan below (correct, just
            # unaccelerated — a rare combination).
            th_global_only = is_global_th and "token" not in scopes_for[SRC_TRADE_HISTORY]

            def _delta_pair(g_src: str, t_src: str, g_alias: str, t_alias: str) -> str:
                parts: list[str] = []
                if "global" in scopes_for[SRC_TRADE_HISTORY]:
                    parts.append(f"{g_src} - lagInFrame({g_src}, 1, 0) OVER w AS {g_alias}")
                if "token" in scopes_for[SRC_TRADE_HISTORY]:
                    parts.append(f"{t_src} - lagInFrame({t_src}, 1, 0) OVER w AS {t_alias}")
                return ",\n                   ".join(parts)
            delta_proj = _delta_pair("cum_pnl_g", "cum_pnl_t", "daily_pnl_g", "daily_pnl_t")
            delta_proj += ",\n                   " + _delta_pair(
                "cum_vol_g", "cum_vol_t", "daily_vol_g", "daily_vol_t")
            delta_proj += ",\n                   " + _delta_pair(
                "cum_trades_g", "cum_trades_t", "daily_trades_g", "daily_trades_t")

            th_window = (
                "                WHERE time >= {sel_since:DateTime} - INTERVAL " + str(th_max + 1) + " DAY\n"
                "                  AND time <  {sel_until:DateTime}\n")
            if th_global_only:
                # Pre-aggregated rollup: one row per (day, wallet), token
                # dimension already summed (HIP3 excluded at build). sumMerge
                # collapses the AggregatingMergeTree parts to the per-(d,wallet)
                # cumulative; the lagInFrame delta below is unchanged.
                cum_subquery = (
                    "                SELECT day AS d, wallet,\n"
                    "                       sumMerge(net_pnl_state)     AS cum_pnl_g,\n"
                    "                       sumMerge(volume_state)      AS cum_vol_g,\n"
                    "                       sumMerge(trade_count_state) AS cum_trades_g\n"
                    "                FROM tradernick.hl_trade_history_wallet_daily\n"
                    "                WHERE day >= toDate({sel_since:DateTime}) - " + str(th_max + 1) + "\n"
                    "                  AND day <  toDate({sel_until:DateTime})\n"
                    "                GROUP BY day, wallet")
            elif is_global_th:
                # Mixed global+token: FINAL-dedup + sum token snapshots into
                # per-(d,wallet) cumulatives (token-less rollup can't serve the
                # _t projection, so this node pays the source scan).
                cum_direct = proj_pair(
                    "sum(net_pnl)",
                    "sumIf(net_pnl, token = {sel_token:String})",
                    SRC_TRADE_HISTORY, "cum_pnl_g", "cum_pnl_t")
                cum_direct += ",\n                       " + proj_pair(
                    "sum(volume)",
                    "sumIf(volume, token = {sel_token:String})",
                    SRC_TRADE_HISTORY, "cum_vol_g", "cum_vol_t")
                cum_direct += ",\n                       " + proj_pair(
                    "sum(trade_count)",
                    "sumIf(trade_count, token = {sel_token:String})",
                    SRC_TRADE_HISTORY, "cum_trades_g", "cum_trades_t")
                cum_subquery = (
                    "                SELECT toDate(time) AS d, wallet,\n"
                    "                       " + cum_direct + "\n"
                    "                FROM tradernick.hl_trade_history FINAL\n"
                    + th_window +
                    f"                  {HIP3_EXCLUDE}\n"
                    "                GROUP BY d, wallet")
            else:
                # Token-prefiltered argMax dedup (keeps the projection prune).
                cum_proj = proj_pair(
                    "sum(cum_net_pnl)",
                    "sumIf(cum_net_pnl, token = {sel_token:String})",
                    SRC_TRADE_HISTORY, "cum_pnl_g", "cum_pnl_t")
                cum_proj += ",\n                       " + proj_pair(
                    "sum(cum_volume)",
                    "sumIf(cum_volume, token = {sel_token:String})",
                    SRC_TRADE_HISTORY, "cum_vol_g", "cum_vol_t")
                cum_proj += ",\n                       " + proj_pair(
                    "sum(cum_trades)",
                    "sumIf(cum_trades, token = {sel_token:String})",
                    SRC_TRADE_HISTORY, "cum_trades_g", "cum_trades_t")
                cum_subquery = (
                    "                SELECT d, wallet,\n"
                    "                       " + cum_proj + "\n"
                    "                FROM (\n"
                    "                    SELECT toDate(time) AS d, wallet, token,\n"
                    "                           argMax(net_pnl, (time, ingested_at))     AS cum_net_pnl,\n"
                    "                           argMax(volume, (time, ingested_at))      AS cum_volume,\n"
                    "                           argMax(trade_count, (time, ingested_at)) AS cum_trades\n"
                    "                    FROM tradernick.hl_trade_history\n"
                    "                    WHERE time >= {sel_since:DateTime} - INTERVAL " + str(th_max + 1) + " DAY\n"
                    "                      AND time <  {sel_until:DateTime}\n"
                    "                      AND token = {sel_token:String}\n"
                    f"                      {HIP3_EXCLUDE}\n"
                    "                    GROUP BY d, wallet, token\n"
                    "                )\n"
                    "                GROUP BY d, wallet")
            ctes.append(
                "daily_per_wallet AS (\n"
                "            SELECT d, wallet,\n"
                "                   " + delta_proj + "\n"
                "            FROM (\n"
                + cum_subquery + "\n"
                "            )\n"
                "            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC\n"
                "                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)\n"
                "        )"
            )

            # trailing — one windowed aggregate per (scope, lookback) combo,
            # gated by sumIf so a single CROSS JOIN over the max window serves
            # every lookback. (Sharpe's daily-return series is built separately
            # in returns_trailing, since it needs the OI capital base too.)
            trailing_parts: list[str] = []
            for (s, L) in sorted(combos_for[SRC_TRADE_HISTORY]):
                w = f"src.d >= target.d - {L}"
                trailing_parts += [
                    f"sumIf(src.daily_pnl_{s}, {w}) AS realized_pnl_{s}_l{L}",
                    f"sumIf(src.daily_vol_{s}, {w}) AS vol_{s}_l{L}",
                    f"sumIf(src.daily_trades_{s}, {w}) AS trade_count_{s}_l{L}",
                ]
            # Prune dormant wallet-days from the CROSS JOIN input. Daily absolute
            # snapshots are DENSE — once a wallet has ever traded, it carries a
            # row for EVERY day (incl. fully inactive ones), so the global
            # daily_per_wallet is ~all wallets × every day (~10M rows / 15d). A
            # dormant day has all-zero deltas and adds 0 to every trailing sum, so
            # dropping it from the join is exact for these sum-based metrics while
            # collapsing the (target_days × dense-wallet-days) cross product back
            # to the sparse active set — that product, GROUP BY'd per (day,
            # wallet), is what OOM'd the AggregatingTransform (~110 GiB) in global
            # scope. Funding-only days survive (their PnL delta is non-zero).
            # Sharpe is unaffected: returns_per_wallet_day reads the DENSE
            # daily_per_wallet directly (a held-but-flat day is a real 0% return),
            # not this CTE.
            th_active_cols: list[str] = []
            for s in sorted(self._suffix(sc) for sc in scopes_for[SRC_TRADE_HISTORY]):
                th_active_cols += [
                    f"src.daily_pnl_{s}", f"src.daily_vol_{s}", f"src.daily_trades_{s}"]
            th_active_pred = " OR ".join(f"{c} != 0" for c in th_active_cols)
            ctes.append(
                "trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(trailing_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN daily_per_wallet src\n"
                "            WHERE src.d >= target.d - " + str(th_max) + "\n"
                "              AND src.d <  target.d\n"
                "              AND (" + th_active_pred + ")\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── eod_wallet ──────────────────────────────────────────────
        if scopes_for[SRC_EOD]:
            # Inner argMaxMerge per (day, wallet, token, side) — then the
            # outer sums collapse across (token, side) to wallet-level
            # totals. Both scopes computed off the same inner row set.
            eod_parts: list[str] = []
            if "global" in scopes_for[SRC_EOD]:
                eod_parts.append("sum(eod_pnl) AS unrealized_pnl_g")
            if "token" in scopes_for[SRC_EOD]:
                eod_parts.append("sumIf(eod_pnl, token = {sel_token:String}) AS unrealized_pnl_t")
            ctes.append(
                "unrealized_eod AS (\n"
                "            SELECT snap_day + INTERVAL 1 DAY AS day, wallet,\n"
                "                   " + ",\n                   ".join(eod_parts) + "\n"
                "            FROM (\n"
                "                SELECT day AS snap_day, wallet, token, side,\n"
                "                       argMaxMerge(pnl_state) AS eod_pnl\n"
                "                FROM tradernick.hl_position_history_eod_wallet\n"
                "                WHERE day >= toDate({sel_since:DateTime}) - INTERVAL 1 DAY\n"
                "                  AND day <  toDate({sel_until:DateTime})\n"
                f"                  {HIP3_EXCLUDE}\n"
                "                GROUP BY snap_day, wallet, token, side\n"
                "            )\n"
                "            GROUP BY day, wallet\n"
                "        )"
            )

        # ── sided_pnl ───────────────────────────────────────────────
        if combos_for[SRC_SIDED]:
            sided_max = src_max_lb(SRC_SIDED)
            sided_day_parts: list[str] = []
            if "global" in scopes_for[SRC_SIDED]:
                sided_day_parts.append("sumMerge(pnl_state) AS day_pnl_g")
            if "token" in scopes_for[SRC_SIDED]:
                sided_day_parts.append(
                    "sumMergeIf(pnl_state, token = {sel_token:String}) AS day_pnl_t")
            sided_token_filter = ""
            if "global" not in scopes_for[SRC_SIDED] and "token" in scopes_for[SRC_SIDED]:
                sided_token_filter = "              AND token = {sel_token:String}\n"
            ctes.append(
                "sided_pnl_daily AS (\n"
                "            SELECT day, wallet, side,\n"
                "                   " + ",\n                   ".join(sided_day_parts) + "\n"
                "            FROM tradernick.hl_fills_pnl_daily\n"
                "            WHERE day >= toDate({sel_since:DateTime}) - INTERVAL " + str(sided_max) + " DAY\n"
                "              AND day <  toDate({sel_until:DateTime})\n"
                + sided_token_filter +
                f"              {HIP3_EXCLUDE}\n"
                "            GROUP BY day, wallet, side\n"
                "        )"
            )

            sided_trail_parts: list[str] = []
            for (s, L) in sorted(combos_for[SRC_SIDED]):
                w = f"src.day >= target.d - {L}"
                sided_trail_parts += [
                    f"sumIf(src.day_pnl_{s}, src.side='long'  AND {w}) AS long_pnl_{s}_l{L}",
                    f"sumIf(src.day_pnl_{s}, src.side='short' AND {w}) AS short_pnl_{s}_l{L}",
                ]
            ctes.append(
                "sided_pnl AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(sided_trail_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN sided_pnl_daily src\n"
                "            WHERE src.day >= target.d - " + str(sided_max) + "\n"
                "              AND src.day <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── funding (per-day per-wallet accrued funding PnL) ────────
        if combos_for[SRC_FUNDING]:
            fund_max = src_max_lb(SRC_FUNDING)
            # Daily per-wallet collapse — sum across tokens for global,
            # sumIf to one token for token scope. Prefilter on token when
            # only token scope is requested (hl_funding_daily ORDER BY is
            # (day, wallet, token), so the token filter is most efficient
            # on the GROUP BY layer, but pushing it into WHERE doesn't
            # hurt and reads cleaner alongside the other sources).
            fund_inner_token_filter = ""
            if "global" not in scopes_for[SRC_FUNDING] and "token" in scopes_for[SRC_FUNDING]:
                fund_inner_token_filter = "              AND token = {sel_token:String}\n"
            fund_day_proj = proj_pair(
                "sumMerge(funding_pnl_state)",
                "sumMergeIf(funding_pnl_state, token = {sel_token:String})",
                SRC_FUNDING, "funding_pnl_g", "funding_pnl_t")
            ctes.append(
                "funding_per_wallet_day AS (\n"
                "            SELECT day, wallet,\n"
                "                   " + fund_day_proj + "\n"
                "            FROM tradernick.hl_funding_daily\n"
                "            WHERE day >= toDate({sel_since:DateTime}) - INTERVAL " + str(fund_max) + " DAY\n"
                "              AND day <  toDate({sel_until:DateTime})\n"
                f"{fund_inner_token_filter}"
                f"              {HIP3_EXCLUDE}\n"
                "            GROUP BY day, wallet\n"
                "        )"
            )
            fund_trail_parts: list[str] = []
            for (s, L) in sorted(combos_for[SRC_FUNDING]):
                w = f"src.day >= target.d - {L}"
                fund_trail_parts.append(
                    f"sumIf(src.funding_pnl_{s}, {w}) AS funding_pnl_{s}_l{L}")
            ctes.append(
                "funding_trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(fund_trail_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN funding_per_wallet_day src\n"
                "            WHERE src.day >= target.d - " + str(fund_max) + "\n"
                "              AND src.day <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── vol_daily / vol (sided + taker fill volumes) ────────────
        if combos_for[SRC_VOL]:
            vol_max = src_max_lb(SRC_VOL)
            # Per-(day, wallet) collapse — sum across (token, position_side)
            # for global; sumIf for token; per-direction columns derived
            # via sumStateIf merging. Six volume dimensions emitted per
            # scope: vol, long_vol, short_vol, taker_buy_vol, taker_sell_vol,
            # each in token + USD.
            vol_day_parts: list[str] = []
            for global_expr, token_expr, g_alias, t_alias in [
                ("sumMerge(vol_token_state)",
                 "sumMergeIf(vol_token_state, token = {sel_token:String})",
                 "vol_token_g", "vol_token_t"),
                ("sumMerge(vol_usd_state)",
                 "sumMergeIf(vol_usd_state, token = {sel_token:String})",
                 "vol_usd_g", "vol_usd_t"),
                ("sumMergeIf(vol_token_state, position_side = 'long')",
                 "sumMergeIf(vol_token_state, position_side = 'long' AND token = {sel_token:String})",
                 "long_vol_token_g", "long_vol_token_t"),
                ("sumMergeIf(vol_usd_state, position_side = 'long')",
                 "sumMergeIf(vol_usd_state, position_side = 'long' AND token = {sel_token:String})",
                 "long_vol_usd_g", "long_vol_usd_t"),
                ("sumMergeIf(vol_token_state, position_side = 'short')",
                 "sumMergeIf(vol_token_state, position_side = 'short' AND token = {sel_token:String})",
                 "short_vol_token_g", "short_vol_token_t"),
                ("sumMergeIf(vol_usd_state, position_side = 'short')",
                 "sumMergeIf(vol_usd_state, position_side = 'short' AND token = {sel_token:String})",
                 "short_vol_usd_g", "short_vol_usd_t"),
                ("sumMerge(taker_buy_vol_token_state)",
                 "sumMergeIf(taker_buy_vol_token_state, token = {sel_token:String})",
                 "taker_buy_vol_token_g", "taker_buy_vol_token_t"),
                ("sumMerge(taker_buy_vol_usd_state)",
                 "sumMergeIf(taker_buy_vol_usd_state, token = {sel_token:String})",
                 "taker_buy_vol_usd_g", "taker_buy_vol_usd_t"),
                ("sumMerge(taker_sell_vol_token_state)",
                 "sumMergeIf(taker_sell_vol_token_state, token = {sel_token:String})",
                 "taker_sell_vol_token_g", "taker_sell_vol_token_t"),
                ("sumMerge(taker_sell_vol_usd_state)",
                 "sumMergeIf(taker_sell_vol_usd_state, token = {sel_token:String})",
                 "taker_sell_vol_usd_g", "taker_sell_vol_usd_t"),
            ]:
                vol_day_parts.append(
                    proj_pair(global_expr, token_expr, SRC_VOL, g_alias, t_alias))
            vol_day_parts = [p for p in vol_day_parts if p]
            # Same prefilter trick as oi_snapshots — when only token
            # scope is needed, push `token = sel_token` into the source
            # WHERE so CH skips other tokens entirely (the table's
            # ORDER BY puts `token` in the 3rd slot after day/wallet, so
            # the prefilter still helps via the index but less so than
            # on the 1h MV).
            vol_inner_token_filter = ""
            if "global" not in scopes_for[SRC_VOL] and "token" in scopes_for[SRC_VOL]:
                vol_inner_token_filter = "              AND token = {sel_token:String}\n"
            ctes.append(
                "vol_per_wallet_day AS (\n"
                "            SELECT day, wallet,\n"
                "                   " + ",\n                   ".join(vol_day_parts) + "\n"
                "            FROM tradernick.hl_fills_vol_daily\n"
                "            WHERE day >= toDate({sel_since:DateTime}) - INTERVAL " + str(vol_max) + " DAY\n"
                "              AND day <  toDate({sel_until:DateTime})\n"
                f"{vol_inner_token_filter}"
                f"              {HIP3_EXCLUDE}\n"
                "            GROUP BY day, wallet\n"
                "        )"
            )
            # Trailing sum per (scope, lookback) combo.
            vol_trail_parts: list[str] = []
            for (s, L) in sorted(combos_for[SRC_VOL]):
                w = f"src.day >= target.d - {L}"
                for col_name in VOL_COLS:
                    vol_trail_parts.append(
                        f"sumIf(src.{col_name}_{s}, {w}) AS {col_name}_{s}_l{L}")
            ctes.append(
                "vol_trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(vol_trail_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN vol_per_wallet_day src\n"
                "            WHERE src.day >= target.d - " + str(vol_max) + "\n"
                "              AND src.day <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── avg OI over hourly snapshots ────────────────────────────
        if combos_for[SRC_OI]:
            oi_max = src_max_lb(SRC_OI)
            OI_AVG_COLS = ["total_oi_token", "long_oi_token", "short_oi_token",
                           "total_oi_usd", "long_oi_usd", "short_oi_usd"]
            # GLOBAL OI with NO token-scope OI (mirrors th_global_only): read the
            # pre-aggregated per-(day,wallet) GLOBAL OI rollup
            # (hl_position_history_oi_wallet_daily) directly. It already holds
            # exactly what the source build emits for global scope, so oi_trailing
            # below is unchanged and the all-token oi_snapshots/oi_per_bucket scan
            # is skipped. ("global" is in scopes_for[SRC_OI] only when a global OI
            # METRIC needs it — global-OI-only-for-Sharpe was discarded earlier
            # and uses oi_cap_daily instead.) Mixed global+token keeps the source
            # build (rare).
            oi_metric_rollup = ("global" in scopes_for[SRC_OI]
                                and "token" not in scopes_for[SRC_OI])
            if oi_metric_rollup:
                oi_b_merges = ",\n                   ".join(
                    f"sumMerge(s_{c}_state) AS s_{c}_g" for c in OI_AVG_COLS)
                ctes.append(
                    "oi_per_day AS (\n"
                    "            SELECT day AS d, wallet,\n"
                    "                   " + oi_b_merges + ",\n"
                    "                   sumMerge(s_roe_state)             AS s_roe_g,\n"
                    "                   sumMerge(s_n_positions_state)     AS s_n_positions_g,\n"
                    "                   uniqExactIfMerge(n_buckets_state) AS n_buckets,\n"
                    "                   argMaxIfMerge(last_total_oi_usd_state)   AS last_total_oi_usd_g,\n"
                    "                   argMaxIfMerge(last_total_oi_token_state) AS last_total_oi_token_g,\n"
                    "                   argMaxIfMerge(last_n_positions_state)    AS last_n_positions_g,\n"
                    "                   maxIfMerge(last_bucket_state)     AS last_bucket\n"
                    "            FROM tradernick.hl_position_history_oi_wallet_daily\n"
                    "            WHERE day >= toDate({sel_since:DateTime}) - INTERVAL " + str(oi_max) + " DAY\n"
                    "              AND day <  toDate({sel_until:DateTime})\n"
                    "            GROUP BY day, wallet\n"
                    "        )"
                )
            else:
              # Inner: argMaxMerge the hourly snapshot per (bucket, token,
              # side, wallet). Middle: collapse to per-(bucket, wallet)
              # totals + sided sums; emit per-scope projections of both
              # amount (tokens) and size (USD). Outer (oi_trailing):
              # average those per-bucket wallet OIs across all hourly
              # buckets in the trailing lookback window.
              # Prefilter on token when only token-scope metrics are
              # referenced — hl_position_history_1h's ORDER BY starts with
              # `token`, so filtering on it lets CH skip all other tokens'
              # parts entirely. Without this, a 7-day NEAR-scoped query
              # reads ~50M rows × 30 tokens; with it, ~1-12M rows × 1
              # token. The global path can't prefilter (it needs all
              # tokens summed), so it pays the larger read.
              oi_inner_token_filter = ""
              if "global" not in scopes_for[SRC_OI] and "token" in scopes_for[SRC_OI]:
                oi_inner_token_filter = "              AND token = {sel_token:String}\n"
              ctes.append(
                "oi_snapshots AS (\n"
                "            SELECT bucket, token, side, wallet,\n"
                "                   argMaxMerge(amount_state) AS amt,\n"
                "                   argMaxMerge(size_state)   AS sz,\n"
                "                   argMaxMerge(pnl_state)    AS pnl\n"
                "            FROM tradernick.hl_position_history_1h\n"
                "            WHERE bucket >= {sel_since:DateTime} - INTERVAL " + str(oi_max) + " DAY\n"
                "              AND bucket <  {sel_until:DateTime}\n"
                f"{oi_inner_token_filter}"
                f"              {HIP3_EXCLUDE}\n"
                "            GROUP BY bucket, token, side, wallet\n"
                "        )"
              )
              # Per-bucket per-wallet sums. Six numeric columns per scope:
              # total/long/short × tokens/USD. Token filter only applies on
              # _t projections via sumIf.
              oi_bucket_parts: list[str] = []
              def _pp(g_expr, t_expr, g_alias, t_alias):
                return proj_pair(g_expr, t_expr, SRC_OI, g_alias, t_alias)
              oi_bucket_parts.append(_pp(
                "sum(amt)",
                "sumIf(amt, token = {sel_token:String})",
                "total_oi_token_g", "total_oi_token_t"))
              oi_bucket_parts.append(_pp(
                "sumIf(amt, side='long')",
                "sumIf(amt, side='long' AND token = {sel_token:String})",
                "long_oi_token_g", "long_oi_token_t"))
              oi_bucket_parts.append(_pp(
                "sumIf(amt, side='short')",
                "sumIf(amt, side='short' AND token = {sel_token:String})",
                "short_oi_token_g", "short_oi_token_t"))
              oi_bucket_parts.append(_pp(
                "sum(sz)",
                "sumIf(sz, token = {sel_token:String})",
                "total_oi_usd_g", "total_oi_usd_t"))
              oi_bucket_parts.append(_pp(
                "sumIf(sz, side='long')",
                "sumIf(sz, side='long' AND token = {sel_token:String})",
                "long_oi_usd_g", "long_oi_usd_t"))
              oi_bucket_parts.append(_pp(
                "sumIf(sz, side='short')",
                "sumIf(sz, side='short' AND token = {sel_token:String})",
                "short_oi_usd_g", "short_oi_usd_t"))
              # Wallet-total unrealized PnL at this bucket (USD). Paired
              # with total_oi_usd in the trailing CTE to compute per-bucket
              # RoE, which is then averaged over the lookback.
              oi_bucket_parts.append(_pp(
                "sum(pnl)",
                "sumIf(pnl, token = {sel_token:String})",
                "unrealized_pnl_usd_g", "unrealized_pnl_usd_t"))
              # Number of distinct open positions (coins held) at this bucket —
              # uniqExact over tokens with a non-zero snapshot. Averaged over the
              # lookback in oi_trailing → avg_position_count. amt is unsigned
              # (side carries direction), so amt > 0 = an open position.
              oi_bucket_parts.append(_pp(
                "uniqExactIf(token, amt > 0)",
                "uniqExactIf(token, amt > 0 AND token = {sel_token:String})",
                "n_positions_g", "n_positions_t"))
              oi_bucket_parts = [p for p in oi_bucket_parts if p]
              ctes.append(
                "oi_per_bucket AS (\n"
                "            SELECT bucket, wallet,\n"
                "                   " + ",\n                   ".join(oi_bucket_parts) + "\n"
                "            FROM oi_snapshots\n"
                "            GROUP BY bucket, wallet\n"
                "        )"
              )
              # Roll the hourly buckets up to one row per (day, wallet) FIRST —
              # the per-day sums + bucket count are sufficient to reconstruct the
              # exact trailing average (avg over buckets = Σ bucket values / Σ
              # bucket count). This shrinks the trailing CROSS JOIN input ~24× vs
              # joining target_days against hourly buckets directly.
              oi_letters = sorted({s for (s, _) in combos_for[SRC_OI]})
              oi_day_parts: list[str] = []
              for s in oi_letters:
                for col_name in OI_AVG_COLS:
                    oi_day_parts.append(f"sum({col_name}_{s}) AS s_{col_name}_{s}")
                oi_day_parts.append(
                    f"sum(if(total_oi_usd_{s} > 0, "
                    f"unrealized_pnl_usd_{s} / total_oi_usd_{s}, 0)) AS s_roe_{s}")
                oi_day_parts.append(f"sum(n_positions_{s}) AS s_n_positions_{s}")
                # Latest-snapshot variants: the value at this day's most
                # recent bucket (argMax by bucket). last_bucket below pairs
                # with these so the trailing CTE can pick the single most
                # recent day's value across the window.
                oi_day_parts.append(
                    f"argMax(total_oi_usd_{s}, bucket) AS last_total_oi_usd_{s}")
                oi_day_parts.append(
                    f"argMax(total_oi_token_{s}, bucket) AS last_total_oi_token_{s}")
                oi_day_parts.append(
                    f"argMax(n_positions_{s}, bucket) AS last_n_positions_{s}")
              ctes.append(
                "oi_per_day AS (\n"
                "            SELECT toDate(bucket) AS d, wallet,\n"
                "                   " + ",\n                   ".join(oi_day_parts) + ",\n"
                "                   count() AS n_buckets,\n"
                "                   max(bucket) AS last_bucket\n"
                "            FROM oi_per_bucket\n"
                "            GROUP BY d, wallet\n"
                "        )"
              )
            # Trailing: avg per-bucket wallet OI over the lookback ending at
            # target.d (exclusive), per (scope, lookback) combo. avg over the
            # window = Σ(per-day bucket sums) / Σ(per-day bucket counts).
            # avg_roe_pct is the per-snapshot ratio averaged the same way.
            oi_trail_parts: list[str] = []
            for (s, L) in sorted(combos_for[SRC_OI]):
                w = f"src.d >= target.d - {L}"
                denom = f"sumIf(src.n_buckets, {w})"
                for col_name in OI_AVG_COLS:
                    oi_trail_parts.append(
                        f"if({denom} > 0, sumIf(src.s_{col_name}_{s}, {w}) / {denom}, 0) "
                        f"AS avg_{col_name}_{s}_l{L}")
                oi_trail_parts.append(
                    f"if({denom} > 0, sumIf(src.s_roe_{s}, {w}) / {denom}, 0) "
                    f"AS avg_roe_pct_{s}_l{L}")
                oi_trail_parts.append(
                    f"if({denom} > 0, sumIf(src.s_n_positions_{s}, {w}) / {denom}, 0) "
                    f"AS avg_n_positions_{s}_l{L}")
                # Latest-snapshot: value at the window's most recent bucket —
                # argMaxIf over per-day last-bucket values, ordered by the
                # day's last_bucket. Wallet absent from the window → 0.
                oi_trail_parts.append(
                    f"argMaxIf(src.last_total_oi_usd_{s}, src.last_bucket, {w}) "
                    f"AS last_total_oi_usd_{s}_l{L}")
                oi_trail_parts.append(
                    f"argMaxIf(src.last_total_oi_token_{s}, src.last_bucket, {w}) "
                    f"AS last_total_oi_token_{s}_l{L}")
                oi_trail_parts.append(
                    f"argMaxIf(src.last_n_positions_{s}, src.last_bucket, {w}) "
                    f"AS last_n_positions_{s}_l{L}")
            ctes.append(
                "oi_trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(oi_trail_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN oi_per_day src\n"
                "            WHERE src.d >= target.d - " + str(oi_max) + "\n"
                "              AND src.d <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── returns (daily-return series for the Sharpe metrics) ────
        # return[d] = daily PnL / that day's avg total OI ($). Two PnL kinds:
        #   realized — daily realized-PnL delta (daily_per_wallet) only.
        #   total    — realized delta + mark-to-market unrealized delta
        #              (EOD unrealized[d] − unrealized[d−1], from
        #              hl_position_history_eod_wallet). When a position closes,
        #              its unrealized converts to realized, so the two deltas sum
        #              to the true day-over-day equity change with no double
        #              count. INNER joined to OI so only days the wallet held
        #              capital (OI>0) count; returns_trailing keeps per-(kind,
        #              scope,lookback) running sums (Σr, Σr², #days).
        sharpe_combos = self._sharpe_combos()
        if sharpe_combos:
            sharpe_max = max((L for (_, _, L) in sharpe_combos), default=1)
            sharpe_scopes = sorted({s for (_, s, _) in sharpe_combos})
            kind_scopes = sorted({(k, s) for (k, s, _) in sharpe_combos})
            total_scopes = sorted({s for (k, s, _) in sharpe_combos if k == "total"})

            # Per-(wallet, day) EOD unrealized DELTA for the `total` kind. The
            # delta diffs against the previous PRESENT snapshot (lagInFrame over
            # the wallet's EOD days), NOT the fixed calendar d−1: the EOD series
            # has gaps (missing snapshot days), and diffing against a 0-filled
            # d−1 would mis-attribute the whole accumulated unrealized level as a
            # single-day move. Reach back sharpe_max+2 days so the earliest
            # in-window day has a prior snapshot. Token-prefiltered when only
            # token scope. (Closing a position books its PnL as realized, so the
            # realized delta + this unrealized delta sum to the true equity move.)
            if total_scopes:
                lvl_parts: list[str] = []
                if "g" in total_scopes:
                    lvl_parts.append("sum(eod) AS unreal_g")
                if "t" in total_scopes:
                    lvl_parts.append("sumIf(eod, token = {sel_token:String}) AS unreal_t")
                delta_parts = [
                    f"unreal_{s} - lagInFrame(unreal_{s}, 1, 0) OVER w AS unreal_delta_{s}"
                    for s in total_scopes
                ]
                eod_tok_filter = ("              AND token = {sel_token:String}\n"
                                  if total_scopes == ["t"] else "")
                ctes.append(
                    "eod_unreal_per_day AS (\n"
                    "            SELECT d, wallet,\n"
                    "                   " + ",\n                   ".join(delta_parts) + "\n"
                    "            FROM (\n"
                    "                SELECT day AS d, wallet,\n"
                    "                       " + ",\n                       ".join(lvl_parts) + "\n"
                    "                FROM (\n"
                    "                    SELECT day, wallet, token, side,\n"
                    "                           argMaxMerge(pnl_state) AS eod\n"
                    "                    FROM tradernick.hl_position_history_eod_wallet\n"
                    "                    WHERE day >= toDate({sel_since:DateTime}) - INTERVAL " + str(sharpe_max + 2) + " DAY\n"
                    "                      AND day <  toDate({sel_until:DateTime})\n"
                    + ("        " + eod_tok_filter if eod_tok_filter else "") +
                    f"                      {HIP3_EXCLUDE}\n"
                    "                    GROUP BY day, wallet, token, side\n"
                    "                )\n"
                    "                GROUP BY day, wallet\n"
                    "            )\n"
                    "            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC\n"
                    "                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)\n"
                    "        )"
                )

            # OI capital base (the Sharpe denominator), sourced per scope from
            # the cheapest place:
            #   token  → oi_per_day (token-prefiltered, already built)
            #   global → oi_cap_daily (the per-(day,wallet) OI rollup, Table B),
            #            so global Sharpe never triggers the all-token
            #            oi_per_bucket scan. day_avg_oi reconstructs from the
            #            -IfState states; nullIf guards a no-bucket day.
            if "g" in sharpe_scopes:
                ctes.append(
                    "oi_cap_daily AS (\n"
                    "            SELECT day AS d, wallet,\n"
                    "                   sumMerge(s_total_oi_usd_state)\n"
                    "                     / nullIf(uniqExactIfMerge(n_buckets_state), 0) AS day_avg_oi_g\n"
                    "            FROM tradernick.hl_position_history_oi_wallet_daily\n"
                    "            WHERE day >= toDate({sel_since:DateTime}) - " + str(sharpe_max) + "\n"
                    "              AND day <  toDate({sel_until:DateTime})\n"
                    "            GROUP BY day, wallet\n"
                    "        )"
                )
            ret_day_parts: list[str] = []
            for (k, s) in kind_scopes:
                if k == "realized":
                    pnl = f"th.daily_pnl_{s}"
                else:  # total: realized delta + mark-to-market unrealized delta
                    pnl = f"(th.daily_pnl_{s} + coalesce(u.unreal_delta_{s}, 0))"
                ret_day_parts.append(
                    f"if(oi.day_avg_oi_{s} > 0, {pnl} / oi.day_avg_oi_{s}, 0) "
                    f"AS daily_return_{k}_{s}")
            # Combined capital source carrying day_avg_oi_{s} for every Sharpe
            # scope. One source per scope; if both are referenced, FULL JOIN on
            # (d, wallet) so a day with capital in either scope survives (the
            # absent scope's avg is NULL → its return guards to 0).
            cap_parts: list[str] = []
            if "t" in sharpe_scopes:
                cap_parts.append(
                    "SELECT d, wallet, if(n_buckets > 0, s_total_oi_usd_t / n_buckets, 0) "
                    "AS day_avg_oi_t FROM oi_per_day")
            if "g" in sharpe_scopes:
                cap_parts.append("SELECT d, wallet, day_avg_oi_g FROM oi_cap_daily")
            if len(cap_parts) == 1:
                oi_source = "                " + cap_parts[0]
            else:
                oi_source = (
                    "                SELECT coalesce(a.d, b.d) AS d,\n"
                    "                       coalesce(a.wallet, b.wallet) AS wallet,\n"
                    "                       a.day_avg_oi_t, b.day_avg_oi_g\n"
                    "                FROM (" + cap_parts[0] + ") a\n"
                    "                FULL OUTER JOIN (" + cap_parts[1] + ") b\n"
                    "                  ON a.d = b.d AND a.wallet = b.wallet")
            eod_joins = ""
            if total_scopes:
                eod_joins = (
                    "\n            LEFT JOIN eod_unreal_per_day u "
                    "ON u.d = th.d AND u.wallet = th.wallet")
            ctes.append(
                "returns_per_wallet_day AS (\n"
                "            SELECT th.d AS d, th.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(ret_day_parts) + "\n"
                "            FROM daily_per_wallet th\n"
                "            INNER JOIN (\n"
                + oi_source + "\n"
                "            ) oi ON oi.d = th.d AND oi.wallet = th.wallet"
                + eod_joins + "\n"
                "        )"
            )
            ret_trail_parts: list[str] = []
            for (k, s, L) in sorted(sharpe_combos):
                w = f"src.d >= target.d - {L}"
                r = f"src.daily_return_{k}_{s}"
                ret_trail_parts += [
                    f"sumIf({r}, {w}) AS ret_sum_{k}_{s}_l{L}",
                    f"sumIf({r} * {r}, {w}) AS ret_sumsq_{k}_{s}_l{L}",
                    f"countIf({w}) AS ret_cnt_{k}_{s}_l{L}",
                ]
            ctes.append(
                "returns_trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(ret_trail_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN returns_per_wallet_day src\n"
                "            WHERE src.d >= target.d - " + str(sharpe_max) + "\n"
                "              AND src.d <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── combined ────────────────────────────────────────────────
        # One row per (day, wallet) with a column per referenced
        # (base, scope, lookback) → `{base}_{scope}_l{L}`. The spine is the
        # first present source (priority order below, matching the legacy
        # behaviour); every other source LEFT JOINs on (day, wallet) so its
        # columns coalesce to 0 for wallets it lacks. The eod snapshot is
        # lookback-independent, so its single column is aliased to each
        # referenced lookback (so metrics like total_pnl that mix a windowed
        # realized-PnL with the current unrealized-PnL line up by suffix).
        # (src, alias, cte_name, base_cols, windowed?)
        src_info = [
            (SRC_TRADE_HISTORY, "r", "trailing",
             ["realized_pnl", "vol", "trade_count"], True),
            (SRC_EOD, "u", "unrealized_eod", ["unrealized_pnl"], False),
            (SRC_SIDED, "s", "sided_pnl", ["long_pnl", "short_pnl"], True),
            (SRC_VOL, "v", "vol_trailing", VOL_COLS, True),
            (SRC_FUNDING, "f", "funding_trailing", ["funding_pnl"], True),
            (SRC_OI, "o", "oi_trailing",
             ["avg_total_oi_token", "avg_long_oi_token", "avg_short_oi_token",
              "avg_total_oi_usd", "avg_long_oi_usd", "avg_short_oi_usd",
              "avg_roe_pct", "avg_n_positions",
              "last_total_oi_usd", "last_total_oi_token", "last_n_positions"], True),
        ]
        spine = next(si for si in src_info if combos_for[si[0]])
        spine_alias = spine[1]
        combined_cols = [f"{spine_alias}.day AS day", f"{spine_alias}.wallet AS wallet"]
        combined_from = f"FROM {spine[2]} {spine_alias}"
        for (src, alias, cte_name, bases, windowed) in src_info:
            if not combos_for[src]:
                continue
            is_spine = src == spine[0]
            if not is_spine:
                combined_from += (
                    f"\n            LEFT JOIN {cte_name} {alias} "
                    f"ON {alias}.day = {spine_alias}.day "
                    f"AND {alias}.wallet = {spine_alias}.wallet")
            for (s, L) in sorted(combos_for[src]):
                for base in bases:
                    srccol = f"{alias}.{base}_{s}" + (f"_l{L}" if windowed else "")
                    tgt = f"{base}_{s}_l{L}"
                    # Spine columns are never null; non-spine LEFT-JOIN columns
                    # coalesce to 0 for wallets the joined source lacks.
                    if is_spine:
                        combined_cols.append(f"{srccol} AS {tgt}")
                    else:
                        combined_cols.append(f"coalesce({srccol}, 0) AS {tgt}")

        # Sharpe's daily-return running sums live in returns_trailing, which is
        # not a `src_info` source (it joins two sources). LEFT JOIN it on
        # (day, wallet) and coalesce — wallets with no invested days → 0 sums,
        # which the Sharpe expression's `ret_cnt > 1` guard maps to 0.
        if sharpe_combos:
            combined_from += (
                f"\n            LEFT JOIN returns_trailing rs "
                f"ON rs.day = {spine_alias}.day "
                f"AND rs.wallet = {spine_alias}.wallet")
            for (k, s, L) in sorted(sharpe_combos):
                for base in ("ret_sum", "ret_sumsq", "ret_cnt"):
                    combined_cols.append(
                        f"coalesce(rs.{base}_{k}_{s}_l{L}, 0) AS {base}_{k}_{s}_l{L}")

        ctes.append(
            "combined AS (\n"
            "            SELECT\n                " + ",\n                ".join(combined_cols) + "\n"
            "            " + combined_from + "\n"
            "        )"
        )

        # ── ranked ──────────────────────────────────────────────────
        where_clauses: list[str] = []
        for i, c in enumerate(self.criteria):
            if c.disabled:
                continue
            if c.min is None and c.max is None:
                continue
            expr = self._metric_expr(
                c.metric, self._effective_scope(c.scope),
                self._effective_lookback(c.lookback),
                self._effective_min_days(c.min_days))
            if c.min is not None:
                where_clauses.append(f"({expr}) >= {{sel_crit_min_{i}:Float64}}")
            if c.max is not None:
                where_clauses.append(f"({expr}) <= {{sel_crit_max_{i}:Float64}}")
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        sort_expr = self._metric_expr(
            self.sort_by, self._sort_scope(), self._sort_lookback(),
            self._sort_min_days())

        ctes.append(
            f"ranked AS (\n"
            f"            SELECT day, wallet,\n"
            f"                   row_number() OVER (PARTITION BY day ORDER BY ({sort_expr}) DESC) AS rk\n"
            f"            FROM combined\n"
            f"            WHERE {where_sql}\n"
            f"        )"
        )
        ctes.append(
            # groupArray over a rank-ordered subquery so the wallet array comes
            # out best→worst by the sort metric (the dialog renders it in order;
            # the selection is a top-N, so it must be ranked). arrayIntersect in
            # composite nodes preserves the first operand's order, so a composite
            # filter stays ranked by its first node too.
            "own_wallets AS (\n"
            "            SELECT day, groupArray(wallet) AS wallets\n"
            "            FROM (\n"
            "                SELECT day, wallet FROM ranked\n"
            "                WHERE rk <= {sel_top_n:UInt32}\n"
            "                ORDER BY day, rk\n"
            "            )\n"
            "            GROUP BY day\n"
            "        )"
        )

        params: dict[str, Any] = {
            "sel_since":    since_dt,
            "sel_until":    until_dt,
            "sel_top_n":    self.top_n,
        }
        # sel_token is required whenever ANY token-scoped projection is used
        # (whether from a criterion override or the overall scope).
        token_used = any(sc == "token" for (_, sc, _) in needs)
        if token_used:
            if not self.token:
                raise ValueError("a criterion or the overall scope is 'token' but no token was provided")
            params["sel_token"] = self.token
        for i, c in enumerate(self.criteria):
            if c.disabled:
                continue
            if c.min is not None:
                params[f"sel_crit_min_{i}"] = c.min
            if c.max is not None:
                params[f"sel_crit_max_{i}"] = c.max

        return ctes, params

    # ── composition driver ───────────────────────────────────────────────

    @staticmethod
    def _intersection_cte(name: str, operands: list[str]) -> str:
        """`name(day, wallets)` = per-day arrayIntersect of >=2 operand CTEs.
        INNER JOIN on day: a day missing from any operand drops out, which is
        the correct AND semantic and matches the downstream day-join."""
        arrays = ", ".join(f"t{i}.wallets" for i in range(len(operands)))
        joins = "".join(
            f"\n            INNER JOIN {operands[i]} t{i} ON t{i}.day = t0.day"
            for i in range(1, len(operands))
        )
        return (
            f"{name} AS (\n"
            f"            SELECT t0.day AS day,\n"
            f"                   arrayIntersect({arrays}) AS wallets\n"
            f"            FROM {operands[0]} t0{joins}\n"
            f"        )"
        )

    def _emit(self, since_dt: datetime, until_dt: datetime,
              blocks: dict[str, str], params: dict[str, Any],
              memo: dict[str, str], depth: int) -> str:
        """Recursively emit this node's CTEs into `blocks` (post-order, so the
        WITH list stays dependency-ordered) and return the node's final
        wallet-array CTE name. Dedups identical subtrees by content hash."""
        full_suffix = self._full_suffix()
        if full_suffix in memo:
            return memo[full_suffix]

        child_finals = [
            child._emit(since_dt, until_dt, blocks, params, memo, depth + 1)
            for child in self.refs
        ]

        operands: list[str] = []
        if self.criteria:
            own_suffix = self._own_suffix()
            own_ctes, own_params = self._build_node_ctes(since_dt, until_dt)
            for cte in own_ctes:
                sc = _suffix_sql(cte, own_suffix)
                cname = sc.split(" AS", 1)[0].strip()
                if cname not in blocks:
                    blocks[cname] = sc
            params.update(_suffix_params(own_params, own_suffix))
            operands.append(f"own_wallets_{own_suffix}")
        operands.extend(child_finals)

        if not operands:  # guarded at parse time; defensive only
            raise ValueError("filter node has neither criteria nor refs")
        if len(operands) == 1:
            final = operands[0]
        else:
            final = f"node_wallets_{full_suffix}"
            if final not in blocks:
                blocks[final] = self._intersection_cte(final, operands)

        memo[full_suffix] = final
        return final

    def build_cte(
        self, since_dt: datetime, until_dt: datetime,
        final_name: str = "smart_wallets",
    ) -> tuple[str, str, dict[str, Any]]:
        """Returns (cte_sql, cte_name, params). The root is always wrapped as
        a `<final_name>(day, wallets[])` CTE so downstream consumers are
        unchanged. `final_name` lets the cache layer compose a live sub-CTE
        (`smart_wallets_live`) alongside a cache read. A single node with no
        refs emits behaviour identical to the pre-composition selector."""
        blocks: dict[str, str] = {}
        params: dict[str, Any] = {}
        memo: dict[str, str] = {}
        root_final = self._emit(since_dt, until_dt, blocks, params, memo, depth=0)
        if root_final != final_name:
            blocks[final_name] = (
                f"{final_name} AS (\n"
                f"            SELECT day, wallets FROM {root_final}\n"
                "        )"
            )
        cte_sql = "WITH\n        " + ",\n        ".join(blocks.values())
        return cte_sql, final_name, params

    def root_metrics(self) -> list[tuple[str, str, int, int]]:
        """(metric_key, effective_scope, effective_lookback, min_days) for the
        ROOT node's sort metric + active criteria, deduped, sort metric first.
        Empty when the root is a pure-composite (no own criteria)."""
        if not self.criteria:
            return []
        items = [(self.sort_by, self._sort_scope(), self._sort_lookback(),
                  self._sort_min_days())]
        for c in self.criteria:
            if c.disabled:
                continue
            items.append((c.metric, self._effective_scope(c.scope),
                          self._effective_lookback(c.lookback),
                          self._effective_min_days(c.min_days)))
        out: list[tuple[str, str, int, int]] = []
        seen: set[str] = set()
        for (k, sc, lb, nd) in items:
            if k in seen:
                continue
            seen.add(k)
            out.append((k, sc, lb, nd))
        return out

    def build_root_metrics_query(
        self, since_dt: datetime, until_dt: datetime,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
        """Build a query yielding, per wallet, the ROOT node's metric values as
        the selector computes them (sort + active criteria) — i.e. the exact
        values that admitted a wallet at a given day. Returns (sql, params,
        meta): the SQL has `{m_day:Date}` + `{m_wallets:Array(String)}`
        placeholders the caller binds, and selects `wallet` plus `m0, m1, …`
        (one per entry in `meta`, in order). Returns None for a pure-composite
        root (no own criteria).

        Runs the root's OWN (unsuffixed) CTE chain standalone — it only reads
        from `combined`; the ranked/own_wallets CTEs are present but unreferenced
        (ClickHouse skips evaluating them). The caller restricts to the final
        wallet set via `m_wallets`, so values line up with the actual (possibly
        composite) selection even though we only compute the root's metrics."""
        metrics = self.root_metrics()
        if not metrics:
            return None
        own_ctes, params = self._build_node_ctes(since_dt, until_dt)
        cols = ", ".join(
            f"({self._metric_expr(k, sc, lb, nd)}) AS m{i}"
            for i, (k, sc, lb, nd) in enumerate(metrics)
        )
        sql = (
            "WITH\n        " + ",\n        ".join(own_ctes) + "\n"
            f"        SELECT wallet, {cols}\n"
            "        FROM combined\n"
            "        WHERE day = {m_day:Date} AND wallet IN {m_wallets:Array(String)}"
        )
        meta = [
            {"key": k, "label": METRIC_REGISTRY[k].label, "scope": sc, "lookback": lb}
            for (k, sc, lb, nd) in metrics
        ]
        return sql, params, meta

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "lookback": self.lookback_days,
            "top_n": self.top_n,
            "scope": self.scope,
            "sort_by": self.sort_by,
            "criteria": [
                {
                    "metric": c.metric,
                    "min": c.min,
                    "max": c.max,
                    "scope": c.scope,
                    "lookback": c.lookback,
                    "min_days": c.min_days,
                    "disabled": c.disabled,
                }
                for c in self.criteria
            ],
        }
        if self.refs:
            out["refs"] = [r.summary() for r in self.refs]
        return out
