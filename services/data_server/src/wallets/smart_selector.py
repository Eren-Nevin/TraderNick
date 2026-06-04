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

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


SRC_TRADE_HISTORY = "trade_history"
SRC_EOD = "eod"
SRC_SIDED = "sided"


@dataclass(frozen=True)
class MetricDef:
    """One ranking dimension. `column_sql` is a template — substitute `{s}`
    with 'g' (global) or 't' (token) per the criterion's effective scope to
    get the concrete column expression used in `combined`."""
    key: str
    label: str
    requires: frozenset[str]
    column_sql: str


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
    # Sharpe: mean / stddevPop of daily PnL over the trailing window. The
    # arrayReduce variant lets us compute it from a groupArray captured in
    # the trailing CTE — no extra hl_trade_history scan. stddev=0 → 0 to
    # keep single-day / constant-PnL wallets from being ranked ±inf.
    "sharpe": MetricDef(
        "sharpe", "Sharpe ratio",
        frozenset({SRC_TRADE_HISTORY}),
        ("if(arrayReduce('stddevPop', daily_pnls_{s}) > 0,"
         " arrayReduce('avg', daily_pnls_{s})"
         " / arrayReduce('stddevPop', daily_pnls_{s}),"
         " 0)"),
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

        lookback = obj.get("lookback")
        if not isinstance(lookback, int) or not (1 <= lookback <= 60):
            raise ValueError("selector.lookback must be int in [1, 60]")
        top_n = obj.get("top_n")
        if not isinstance(top_n, int) or not (1 <= top_n <= 500):
            raise ValueError("selector.top_n must be int in [1, 500]")
        scope = obj.get("scope", "global")
        if scope not in ("global", "token"):
            raise ValueError("selector.scope must be 'global' or 'token'")
        sort_by = obj.get("sort_by")
        if not isinstance(sort_by, str) or sort_by not in METRIC_REGISTRY:
            raise ValueError(f"selector.sort_by must be one of: {list(METRIC_REGISTRY)}")

        raw_criteria = obj.get("criteria", [])
        if not isinstance(raw_criteria, list):
            raise ValueError("selector.criteria must be a JSON array")
        criteria: list[SmartCriterion] = []
        for i, c in enumerate(raw_criteria):
            if not isinstance(c, dict):
                raise ValueError(f"selector.criteria[{i}] must be an object")
            metric = c.get("metric")
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
            cdisabled = c.get("disabled", False)
            if not isinstance(cdisabled, bool):
                raise ValueError(f"selector.criteria[{i}].disabled must be a boolean")
            criteria.append(SmartCriterion(
                metric=metric,
                min=float(cmin) if cmin is not None else None,
                max=float(cmax) if cmax is not None else None,
                scope=cscope,
                disabled=cdisabled,
            ))

        return cls(
            lookback_days=lookback,
            top_n=top_n,
            scope=scope,
            sort_by=sort_by,
            criteria=criteria,
            token=token,
        )

    # ── scope resolution ────────────────────────────────────────────────

    def _effective_scope(self, criterion_scope: str | None) -> str:
        return criterion_scope or self.scope

    def _sort_scope(self) -> str:
        """Scope to use when ordering by `sort_by`. If sort_by matches a
        criterion in the list, that criterion's effective scope wins.
        Otherwise the overall scope is the fallback — keeps orphaned
        sort metrics from breaking when they're not in the criteria UI."""
        for c in self.criteria:
            if c.metric == self.sort_by:
                return self._effective_scope(c.scope)
        return self.scope

    def _needs(self) -> dict[tuple[str, str], bool]:
        """Map of (source, scope) → True for every combo any active metric
        references. Disabled criteria don't pull their source in (the
        ranked CTE skips their WHERE clause too); the sort metric always
        counts regardless of any criterion's disabled state."""
        needs: dict[tuple[str, str], bool] = {}
        ss = self._sort_scope()
        for src in METRIC_REGISTRY[self.sort_by].requires:
            needs[(src, ss)] = True
        for c in self.criteria:
            if c.disabled:
                continue
            eff = self._effective_scope(c.scope)
            for src in METRIC_REGISTRY[c.metric].requires:
                needs[(src, eff)] = True
        return needs

    @staticmethod
    def _suffix(scope: str) -> str:
        return "g" if scope == "global" else "t"

    @classmethod
    def _metric_expr(cls, metric_key: str, scope: str) -> str:
        return METRIC_REGISTRY[metric_key].column_sql.replace(
            "{s}", cls._suffix(scope))

    # ── CTE emission ────────────────────────────────────────────────────

    def build_cte(
        self, since_dt: datetime, until_dt: datetime
    ) -> tuple[str, str, dict[str, Any]]:
        """Returns (cte_sql, cte_name, params). See module docstring."""
        needs = self._needs()
        # Per-source presence of each scope
        scopes_for: dict[str, set[str]] = {
            SRC_TRADE_HISTORY: set(),
            SRC_EOD: set(),
            SRC_SIDED: set(),
        }
        for (src, sc) in needs:
            scopes_for[src].add(sc)
        any_source = (scopes_for[SRC_TRADE_HISTORY]
                      or scopes_for[SRC_EOD]
                      or scopes_for[SRC_SIDED])
        if not any_source:
            raise ValueError(
                "selector references no sources — at least one metric "
                "(criterion or sort) must be defined")

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
        # eod or sided. The gate's purpose is to drop chart days whose
        # trailing window starts before any data exists.
        gate_src = (
            "tradernick.hl_trade_history" if scopes_for[SRC_TRADE_HISTORY]
            else "tradernick.hl_position_history_eod_wallet" if scopes_for[SRC_EOD]
            else "tradernick.hl_fills_pnl_daily"
        )
        gate_time_col = "time" if scopes_for[SRC_TRADE_HISTORY] else "day"
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
            "            WHERE d_set.d - {sel_lookback:UInt32} >= data_min.min_d\n"
            "        )"
        )

        # ── trade_history ───────────────────────────────────────────
        if scopes_for[SRC_TRADE_HISTORY]:
            daily_proj = proj_pair(
                "sum(net_pnl)",
                "sumIf(net_pnl, token = {sel_token:String})",
                SRC_TRADE_HISTORY, "daily_pnl_g", "daily_pnl_t")
            daily_proj += ",\n                   " + proj_pair(
                "sum(volume)",
                "sumIf(volume, token = {sel_token:String})",
                SRC_TRADE_HISTORY, "daily_vol_g", "daily_vol_t")
            daily_proj += ",\n                   " + proj_pair(
                "sum(trade_count)",
                "sumIf(trade_count, token = {sel_token:String})",
                SRC_TRADE_HISTORY, "daily_trades_g", "daily_trades_t")
            ctes.append(
                f"daily_per_wallet AS (\n"
                f"            SELECT toDate(time) AS d, wallet,\n"
                f"                   {daily_proj}\n"
                f"            FROM tradernick.hl_trade_history\n"
                f"            WHERE time >= {{sel_since:DateTime}} - INTERVAL {{sel_lookback:UInt32}} DAY\n"
                f"              AND time <  {{sel_until:DateTime}}\n"
                f"            GROUP BY d, wallet\n"
                f"        )"
            )

            # trailing — sum the per-day aggregates across the lookback
            # window, retaining a per-day PnL array for Sharpe.
            trailing_parts: list[str] = []
            if "global" in scopes_for[SRC_TRADE_HISTORY]:
                trailing_parts += [
                    "sum(src.daily_pnl_g) AS realized_pnl_g",
                    "sum(src.daily_vol_g) AS vol_g",
                    "sum(src.daily_trades_g) AS trade_count_g",
                    "groupArray(src.daily_pnl_g) AS daily_pnls_g",
                ]
            if "token" in scopes_for[SRC_TRADE_HISTORY]:
                trailing_parts += [
                    "sum(src.daily_pnl_t) AS realized_pnl_t",
                    "sum(src.daily_vol_t) AS vol_t",
                    "sum(src.daily_trades_t) AS trade_count_t",
                    "groupArray(src.daily_pnl_t) AS daily_pnls_t",
                ]
            ctes.append(
                "trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(trailing_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN daily_per_wallet src\n"
                "            WHERE src.d >= target.d - {sel_lookback:UInt32}\n"
                "              AND src.d <  target.d\n"
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
                "                GROUP BY snap_day, wallet, token, side\n"
                "            )\n"
                "            GROUP BY day, wallet\n"
                "        )"
            )

        # ── sided_pnl ───────────────────────────────────────────────
        if scopes_for[SRC_SIDED]:
            sided_day_parts: list[str] = []
            if "global" in scopes_for[SRC_SIDED]:
                sided_day_parts.append("sumMerge(pnl_state) AS day_pnl_g")
            if "token" in scopes_for[SRC_SIDED]:
                sided_day_parts.append(
                    "sumMergeIf(pnl_state, token = {sel_token:String}) AS day_pnl_t")
            ctes.append(
                "sided_pnl_daily AS (\n"
                "            SELECT day, wallet, side,\n"
                "                   " + ",\n                   ".join(sided_day_parts) + "\n"
                "            FROM tradernick.hl_fills_pnl_daily\n"
                "            WHERE day >= toDate({sel_since:DateTime}) - INTERVAL {sel_lookback:UInt32} DAY\n"
                "              AND day <  toDate({sel_until:DateTime})\n"
                "            GROUP BY day, wallet, side\n"
                "        )"
            )

            sided_trail_parts: list[str] = []
            if "global" in scopes_for[SRC_SIDED]:
                sided_trail_parts += [
                    "sumIf(src.day_pnl_g, src.side='long')  AS long_pnl_g",
                    "sumIf(src.day_pnl_g, src.side='short') AS short_pnl_g",
                ]
            if "token" in scopes_for[SRC_SIDED]:
                sided_trail_parts += [
                    "sumIf(src.day_pnl_t, src.side='long')  AS long_pnl_t",
                    "sumIf(src.day_pnl_t, src.side='short') AS short_pnl_t",
                ]
            ctes.append(
                "sided_pnl AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   " + ",\n                   ".join(sided_trail_parts) + "\n"
                "            FROM target_days target\n"
                "            CROSS JOIN sided_pnl_daily src\n"
                "            WHERE src.day >= target.d - {sel_lookback:UInt32}\n"
                "              AND src.day <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        # ── combined ────────────────────────────────────────────────
        # Spine table is whichever exists; trailing is biggest when it does.
        # Subsequent sources LEFT JOIN onto the spine so wallets missing
        # from those sources show 0.
        combined_cols: list[str] = []
        combined_from = ""
        if scopes_for[SRC_TRADE_HISTORY]:
            combined_cols += ["r.day AS day", "r.wallet AS wallet"]
            if "global" in scopes_for[SRC_TRADE_HISTORY]:
                combined_cols += [
                    "r.realized_pnl_g AS realized_pnl_g",
                    "r.vol_g AS vol_g",
                    "r.trade_count_g AS trade_count_g",
                    "r.daily_pnls_g AS daily_pnls_g",
                ]
            if "token" in scopes_for[SRC_TRADE_HISTORY]:
                combined_cols += [
                    "r.realized_pnl_t AS realized_pnl_t",
                    "r.vol_t AS vol_t",
                    "r.trade_count_t AS trade_count_t",
                    "r.daily_pnls_t AS daily_pnls_t",
                ]
            combined_from = "FROM trailing r"
            spine_alias = "r"
        elif scopes_for[SRC_EOD]:
            combined_cols += ["u.day AS day", "u.wallet AS wallet"]
            combined_from = "FROM unrealized_eod u"
            spine_alias = "u"
        else:
            combined_cols += ["s.day AS day", "s.wallet AS wallet"]
            combined_from = "FROM sided_pnl s"
            spine_alias = "s"

        if scopes_for[SRC_EOD]:
            if spine_alias != "u":
                combined_from += (
                    f"\n            LEFT JOIN unrealized_eod u "
                    f"ON u.day = {spine_alias}.day AND u.wallet = {spine_alias}.wallet")
            if "global" in scopes_for[SRC_EOD]:
                combined_cols.append("coalesce(u.unrealized_pnl_g, 0) AS unrealized_pnl_g")
            if "token" in scopes_for[SRC_EOD]:
                combined_cols.append("coalesce(u.unrealized_pnl_t, 0) AS unrealized_pnl_t")

        if scopes_for[SRC_SIDED]:
            if spine_alias != "s":
                combined_from += (
                    f"\n            LEFT JOIN sided_pnl s "
                    f"ON s.day = {spine_alias}.day AND s.wallet = {spine_alias}.wallet")
            if "global" in scopes_for[SRC_SIDED]:
                combined_cols.append("coalesce(s.long_pnl_g, 0) AS long_pnl_g")
                combined_cols.append("coalesce(s.short_pnl_g, 0) AS short_pnl_g")
            if "token" in scopes_for[SRC_SIDED]:
                combined_cols.append("coalesce(s.long_pnl_t, 0) AS long_pnl_t")
                combined_cols.append("coalesce(s.short_pnl_t, 0) AS short_pnl_t")

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
            expr = self._metric_expr(c.metric, self._effective_scope(c.scope))
            if c.min is not None:
                where_clauses.append(f"({expr}) >= {{sel_crit_min_{i}:Float64}}")
            if c.max is not None:
                where_clauses.append(f"({expr}) <= {{sel_crit_max_{i}:Float64}}")
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        sort_expr = self._metric_expr(self.sort_by, self._sort_scope())

        ctes.append(
            f"ranked AS (\n"
            f"            SELECT day, wallet,\n"
            f"                   row_number() OVER (PARTITION BY day ORDER BY ({sort_expr}) DESC) AS rk\n"
            f"            FROM combined\n"
            f"            WHERE {where_sql}\n"
            f"        )"
        )
        ctes.append(
            "smart_wallets AS (\n"
            "            SELECT day, groupArray(wallet) AS wallets\n"
            "            FROM ranked\n"
            "            WHERE rk <= {sel_top_n:UInt32}\n"
            "            GROUP BY day\n"
            "        )"
        )

        cte_sql = "WITH\n        " + ",\n        ".join(ctes)

        params: dict[str, Any] = {
            "sel_since":    since_dt,
            "sel_until":    until_dt,
            "sel_lookback": self.lookback_days,
            "sel_top_n":    self.top_n,
        }
        # sel_token is required whenever ANY token-scoped projection is used
        # (whether from a criterion override or the overall scope).
        token_used = any(sc == "token" for (_, sc) in needs.keys())
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

        return cte_sql, "smart_wallets", params

    def summary(self) -> dict[str, Any]:
        return {
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
                    "disabled": c.disabled,
                }
                for c in self.criteria
            ],
        }
