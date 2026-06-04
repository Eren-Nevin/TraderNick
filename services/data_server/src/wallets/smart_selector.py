"""HL smart-wallet selector — reusable wallet-ranking module.

Any data_server endpoint that wants to operate on "today's smart HL wallets"
calls SmartSelector.build_cte() to get a CTE SQL fragment + parameters. The
emitted CTE produces a per-day array of wallet addresses (`leaderboard.day`,
`leaderboard.wallets`); the consumer INNER-JOINs on day and has() the wallet
column into the array.

Selection is criteria-based: any number of metric thresholds (min/max), plus
a single sort metric. Lookback is shared across all criteria — we compute
the trailing window once and re-use the same daily aggregates for every
metric a query touches.

Data sources, materialised on-demand based on which metrics are referenced:
  - hl_trade_history          → realized PnL, volume, trade count, Sharpe
  - hl_position_history_eod   → end-of-prior-day unrealized PnL snapshot
  - hl_fills_pnl_daily        → side-specific realized PnL (long_pnl / short_pnl)

Adding a new metric is a single registry entry: declare its key, label, the
sources it needs, and the SQL expression in `combined` that yields its value.
The selector wires the rest (CTE materialisation, criteria filtering, sort).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


# Sources a metric can depend on. Used to decide which CTEs to materialise.
SRC_TRADE_HISTORY = "trade_history"
SRC_EOD = "eod"
SRC_SIDED = "sided"


@dataclass(frozen=True)
class MetricDef:
    """One ranking dimension. `column_sql` is the expression that produces
    this metric's value in the `combined` CTE — it references the per-wallet
    columns that the source CTEs emit."""
    key: str
    label: str
    requires: frozenset[str]
    column_sql: str


# Source-derived columns available in `combined` (one row per (day, wallet))
# after the optional CTEs are joined:
#   from trailing:        realized_pnl, vol, trade_count, daily_pnls (Array)
#   from unrealized_eod:  unrealized_pnl
#   from sided_pnl:       long_pnl, short_pnl
#
# `daily_pnls` is an Array(Float64) of one element per active day in the
# trailing window — used to compute Sharpe. `vol` may be 0 if every metric
# referenced doesn't require trade_history; protect divisions against that.
METRIC_REGISTRY: dict[str, MetricDef] = {
    "pnl_pct": MetricDef(
        "pnl_pct", "PnL %",
        frozenset({SRC_TRADE_HISTORY}),
        "if(vol > 0, realized_pnl / vol, 0)",
    ),
    "realized_pnl": MetricDef(
        "realized_pnl", "Realized PnL ($)",
        frozenset({SRC_TRADE_HISTORY}),
        "realized_pnl",
    ),
    "unrealized_pnl": MetricDef(
        "unrealized_pnl", "Unrealized PnL ($)",
        frozenset({SRC_EOD}),
        "unrealized_pnl",
    ),
    "total_pnl": MetricDef(
        "total_pnl", "Total PnL ($)",
        frozenset({SRC_TRADE_HISTORY, SRC_EOD}),
        "(realized_pnl + unrealized_pnl)",
    ),
    "total_pnl_pct": MetricDef(
        "total_pnl_pct", "Total PnL %",
        frozenset({SRC_TRADE_HISTORY, SRC_EOD}),
        "if(vol > 0, (realized_pnl + unrealized_pnl) / vol, 0)",
    ),
    "volume": MetricDef(
        "volume", "Volume ($)",
        frozenset({SRC_TRADE_HISTORY}),
        "vol",
    ),
    "trade_count": MetricDef(
        "trade_count", "Trade count",
        frozenset({SRC_TRADE_HISTORY}),
        "trade_count",
    ),
    "long_pnl": MetricDef(
        "long_pnl", "Long PnL ($)",
        frozenset({SRC_SIDED}),
        "long_pnl",
    ),
    "short_pnl": MetricDef(
        "short_pnl", "Short PnL ($)",
        frozenset({SRC_SIDED}),
        "short_pnl",
    ),
    # Sharpe: mean(daily_pnl) / stddevPop(daily_pnl) over the active days
    # in the trailing window. stddev=0 → 0 (single-day or constant-PnL
    # wallets), keeps the ranking stable instead of producing ±inf.
    "sharpe": MetricDef(
        "sharpe", "Sharpe ratio",
        frozenset({SRC_TRADE_HISTORY}),
        ("if(arrayReduce('stddevPop', daily_pnls) > 0,"
         " arrayReduce('avg', daily_pnls) / arrayReduce('stddevPop', daily_pnls),"
         " 0)"),
    ),
}


@dataclass
class SmartCriterion:
    metric: str
    min: float | None = None
    max: float | None = None

    def validate(self) -> None:
        if self.metric not in METRIC_REGISTRY:
            raise ValueError(f"unknown metric: {self.metric}")
        if self.min is None and self.max is None:
            # A criterion that has neither bound is a no-op; allow it
            # (lets the UI include a metric in the form before the user
            # types thresholds) but treat it as informational.
            pass


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
        """Parse + validate the `selector` URL param. Raises ValueError on
        any malformed input — caller turns those into 400 responses."""
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
            criteria.append(SmartCriterion(
                metric=metric,
                min=float(cmin) if cmin is not None else None,
                max=float(cmax) if cmax is not None else None,
            ))

        return cls(
            lookback_days=lookback,
            top_n=top_n,
            scope=scope,
            sort_by=sort_by,
            criteria=criteria,
            token=token,
        )

    # ── source-need analysis ────────────────────────────────────────────

    def _referenced_metrics(self) -> set[str]:
        """Metrics that need to materialise their source CTE — the sort
        metric plus every metric mentioned in criteria. (Even no-bound
        criteria are kept so adding/removing min/max doesn't require a
        re-fetch of the schema.)"""
        seen = {self.sort_by}
        for c in self.criteria:
            seen.add(c.metric)
        return seen

    def _needs(self, src: str) -> bool:
        for m in self._referenced_metrics():
            if src in METRIC_REGISTRY[m].requires:
                return True
        return False

    # ── CTE emission ────────────────────────────────────────────────────

    def build_cte(
        self, since_dt: datetime, until_dt: datetime
    ) -> tuple[str, str, dict[str, Any]]:
        """Returns (cte_sql, cte_name, params).

        cte_sql is a `WITH … leaderboard AS (…)` fragment ending without a
        trailing comma — the caller appends a comma + their own CTE(s) or
        a top-level SELECT. cte_name is 'smart_wallets' which the caller
        joins on `day` and filters with `has(wallets, p.wallet)`.

        The fragment uses parameterised placeholders ({since:DateTime},
        {until:DateTime}, etc) so the caller passes them via ch.query's
        parameters dict — never via string-format injection."""
        needs_trade = self._needs(SRC_TRADE_HISTORY)
        needs_eod = self._needs(SRC_EOD)
        needs_sided = self._needs(SRC_SIDED)

        # Token filters. scope=token narrows every source that has a
        # `token` column to just the chart's token; scope=global reads
        # all tokens (a wallet's BTC PnL still counts toward their global
        # rank when the chart shows ETH).
        token_filter_trade = ""
        token_filter_eod = ""
        token_filter_sided = ""
        if self.scope == "token":
            token_filter_trade = "AND token = {sel_token:String}"
            token_filter_eod = "AND token = {sel_token:String}"
            token_filter_sided = "AND token = {sel_token:String}"

        # Coalesce + join logic for the `combined` CTE. trailing is the
        # spine (always exists when needs_trade); eod / sided LEFT-JOIN
        # onto it so wallets missing from those sources show 0.
        combined_select_cols: list[str] = []
        combined_from = ""

        if needs_trade:
            combined_select_cols += [
                "r.day AS day",
                "r.wallet AS wallet",
                "r.realized_pnl AS realized_pnl",
                "r.vol AS vol",
                "r.trade_count AS trade_count",
                "r.daily_pnls AS daily_pnls",
            ]
            combined_from = "FROM trailing r"
        else:
            # Without trade_history we still need a spine of (day, wallet);
            # use whichever optional source is present. eod has both. sided
            # has them too but the spine should prefer the larger set —
            # eod tends to cover more wallets (all open positions) than
            # sided (only those who actually closed trades).
            if needs_eod:
                combined_select_cols += [
                    "u.day AS day",
                    "u.wallet AS wallet",
                    "0.0 AS realized_pnl",
                    "0.0 AS vol",
                    "0  AS trade_count",
                    "CAST([] AS Array(Float64)) AS daily_pnls",
                ]
                combined_from = "FROM unrealized_eod u"
            elif needs_sided:
                combined_select_cols += [
                    "s.day AS day",
                    "s.wallet AS wallet",
                    "0.0 AS realized_pnl",
                    "0.0 AS vol",
                    "0  AS trade_count",
                    "CAST([] AS Array(Float64)) AS daily_pnls",
                ]
                combined_from = "FROM sided_pnl s"
            else:
                raise ValueError(
                    "selector references no sources — at least one metric "
                    "(criterion or sort) must be defined"
                )

        if needs_eod:
            join_key = "u.day = day AND u.wallet = wallet" if needs_trade else None
            if needs_trade:
                combined_from += f"\n            LEFT JOIN unrealized_eod u ON u.day = r.day AND u.wallet = r.wallet"
                combined_select_cols.append("coalesce(u.unrealized_pnl, 0) AS unrealized_pnl")
            else:
                combined_select_cols.append("coalesce(u.unrealized_pnl, 0) AS unrealized_pnl")
        else:
            combined_select_cols.append("0.0 AS unrealized_pnl")

        if needs_sided:
            if needs_trade:
                combined_from += f"\n            LEFT JOIN sided_pnl s ON s.day = r.day AND s.wallet = r.wallet"
            elif needs_eod:
                combined_from += f"\n            LEFT JOIN sided_pnl s ON s.day = u.day AND s.wallet = u.wallet"
            combined_select_cols.append("coalesce(s.long_pnl, 0) AS long_pnl")
            combined_select_cols.append("coalesce(s.short_pnl, 0) AS short_pnl")
        else:
            combined_select_cols.append("0.0 AS long_pnl")
            combined_select_cols.append("0.0 AS short_pnl")

        combined_cte = (
            "combined AS (\n"
            + "            SELECT\n                " + ",\n                ".join(combined_select_cols) + "\n"
            + "            " + combined_from + "\n"
            + "        )"
        )

        # Build ranked CTE's WHERE clauses + ORDER BY from the criteria.
        where_clauses: list[str] = []
        for i, c in enumerate(self.criteria):
            if c.min is None and c.max is None:
                continue
            col = METRIC_REGISTRY[c.metric].column_sql
            if c.min is not None:
                where_clauses.append(f"({col}) >= {{sel_crit_min_{i}:Float64}}")
            if c.max is not None:
                where_clauses.append(f"({col}) <= {{sel_crit_max_{i}:Float64}}")
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        sort_col = METRIC_REGISTRY[self.sort_by].column_sql

        # Stitch the CTEs together. data_min + target_days always emit so
        # the consumer's INNER JOIN naturally drops days without full
        # lookback coverage (no leakage).
        ctes: list[str] = []

        # data_min: earliest available row in whichever source we're using
        # for the spine. trade_history is the most common; eod/sided exist
        # for the same lookback window so any of them works for the gate.
        gate_table = (
            "tradernick.hl_trade_history" if needs_trade
            else "tradernick.hl_position_history_eod_wallet" if needs_eod
            else "tradernick.hl_fills_pnl_daily"
        )
        gate_time_col = "time" if needs_trade else "day"
        gate_token_filter = (
            token_filter_trade if needs_trade
            else token_filter_eod if needs_eod
            else token_filter_sided
        )
        ctes.append(
            f"data_min AS (\n"
            f"            SELECT toDate(min({gate_time_col})) AS min_d\n"
            f"            FROM {gate_table}\n"
            f"            WHERE 1=1 {gate_token_filter}\n"
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

        if needs_trade:
            ctes.append(
                f"daily_per_wallet AS (\n"
                f"            SELECT toDate(time) AS d, wallet,\n"
                f"                   sum(net_pnl)    AS daily_pnl,\n"
                f"                   sum(volume)     AS daily_vol,\n"
                f"                   sum(trade_count) AS daily_trades\n"
                f"            FROM tradernick.hl_trade_history\n"
                f"            WHERE time >= {{sel_since:DateTime}} - INTERVAL {{sel_lookback:UInt32}} DAY\n"
                f"              AND time <  {{sel_until:DateTime}}\n"
                f"              {token_filter_trade}\n"
                f"            GROUP BY d, wallet\n"
                f"        )"
            )
            ctes.append(
                "trailing AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   sum(src.daily_pnl)    AS realized_pnl,\n"
                "                   sum(src.daily_vol)    AS vol,\n"
                "                   sum(src.daily_trades) AS trade_count,\n"
                "                   groupArray(src.daily_pnl) AS daily_pnls\n"
                "            FROM target_days target\n"
                "            CROSS JOIN daily_per_wallet src\n"
                "            WHERE src.d >= target.d - {sel_lookback:UInt32}\n"
                "              AND src.d <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        if needs_eod:
            ctes.append(
                f"unrealized_eod AS (\n"
                f"            SELECT snap_day + INTERVAL 1 DAY AS day, wallet,\n"
                f"                   sum(eod_pnl) AS unrealized_pnl\n"
                f"            FROM (\n"
                f"                SELECT day AS snap_day, wallet, token, side,\n"
                f"                       argMaxMerge(pnl_state) AS eod_pnl\n"
                f"                FROM tradernick.hl_position_history_eod_wallet\n"
                f"                WHERE day >= toDate({{sel_since:DateTime}}) - INTERVAL 1 DAY\n"
                f"                  AND day <  toDate({{sel_until:DateTime}})\n"
                f"                  {token_filter_eod}\n"
                f"                GROUP BY snap_day, wallet, token, side\n"
                f"            )\n"
                f"            GROUP BY day, wallet\n"
                f"        )"
            )

        if needs_sided:
            # Trailing-window sums of sided PnL: read the per-day MV over
            # the lookback pad, then CROSS JOIN target_days to assemble
            # (target.d, wallet) → trailing long_pnl, short_pnl.
            ctes.append(
                f"sided_pnl_daily AS (\n"
                f"            SELECT day, wallet, side,\n"
                f"                   sumMerge(pnl_state) AS day_pnl\n"
                f"            FROM tradernick.hl_fills_pnl_daily\n"
                f"            WHERE day >= toDate({{sel_since:DateTime}}) - INTERVAL {{sel_lookback:UInt32}} DAY\n"
                f"              AND day <  toDate({{sel_until:DateTime}})\n"
                f"              {token_filter_sided}\n"
                f"            GROUP BY day, wallet, side\n"
                f"        )"
            )
            ctes.append(
                "sided_pnl AS (\n"
                "            SELECT target.d AS day, src.wallet AS wallet,\n"
                "                   sumIf(src.day_pnl, src.side='long')  AS long_pnl,\n"
                "                   sumIf(src.day_pnl, src.side='short') AS short_pnl\n"
                "            FROM target_days target\n"
                "            CROSS JOIN sided_pnl_daily src\n"
                "            WHERE src.day >= target.d - {sel_lookback:UInt32}\n"
                "              AND src.day <  target.d\n"
                "            GROUP BY target.d, src.wallet\n"
                "        )"
            )

        ctes.append(combined_cte)
        ctes.append(
            f"ranked AS (\n"
            f"            SELECT day, wallet,\n"
            f"                   row_number() OVER (PARTITION BY day ORDER BY ({sort_col}) DESC) AS rk\n"
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
        if self.scope == "token":
            if not self.token:
                raise ValueError("scope='token' requires a token")
            params["sel_token"] = self.token
        for i, c in enumerate(self.criteria):
            if c.min is not None:
                params[f"sel_crit_min_{i}"] = c.min
            if c.max is not None:
                params[f"sel_crit_max_{i}"] = c.max

        return cte_sql, "smart_wallets", params

    def summary(self) -> dict[str, Any]:
        """JSON-serializable dump for the response's meta field."""
        return {
            "lookback": self.lookback_days,
            "top_n": self.top_n,
            "scope": self.scope,
            "sort_by": self.sort_by,
            "criteria": [
                {"metric": c.metric, "min": c.min, "max": c.max}
                for c in self.criteria
            ],
        }
