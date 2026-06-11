"""Provider taxonomy + role-based filtering for the per-provider split.

The monolithic ingestion service is being split into one container per
(provider × role) pair — see /home/mvp/.claude/plans/async-bouncing-gray.md.
This module is the single source of truth for:

  • which protocol versions group into which provider
    (e.g. AAVE V2 + V3 + V4 → "aave")
  • which streams belong to each provider (derived from `StreamSpec.group`)
  • which backfill job_types belong to each provider (the only place that
    knows that `backfill_aero_basic_events` is owned by aerodrome, etc.)

A container reads INGESTION_PROVIDER + INGESTION_ROLE at startup and
filters its supervisor / JobManager registries against this map. When
both env vars are absent the container falls back to the legacy
"monolith" behaviour — it spawns every stream and accepts every backfill
job_type — so the currently-running monolith is unaffected by this
change.
"""
from __future__ import annotations

import os

# UI group label (set by StreamSpec.group in streams/__init__.py) →
# provider bucket. New protocol versions (AAVE V5 etc.) just add an entry
# here; nothing else moves.
GROUP_TO_PROVIDER: dict[str, str] = {
    "Hyperliquid":      "hyperliquid",
    "Binance":          "binance",
    "Transfers":        "transfers",
    "AAVE V3":          "aave",
    "AAVE V2":          "aave",
    "AAVE V4":          "aave",
    "Uniswap V3":       "uniswap",
    "Uniswap V2":       "uniswap",
    "Uniswap V4":       "uniswap",
    "Aerodrome":        "aerodrome",
    "Aerodrome Basic":  "aerodrome",
    "Lido":             "lido",
    "Morpho":           "morpho",
    "Spark":            "spark",
    "GMX":              "gmx",
    # Added in Phase B — derived-MV refresh ticks (exchange_flow self-heal,
    # HL position MV cascade, fills_pnl_daily). Treated as a sibling
    # provider so its workers show up in the admin overview's stream table.
    "Data process":     "data_process",
}


# Backfill job_type → provider. Keep aligned with jobs/manager.py JOB_MODULES.
# Legacy aliases (e.g. "backfill_aave_events" from the pre-rename era) are
# mapped to their current provider so resume_inflight handles them too.
JOB_TYPE_TO_PROVIDER: dict[str, str] = {
    "backfill_hyperliquid_events":      "hyperliquid",

    "backfill_binance_ohlcv":           "binance",
    "backfill_binance_raw_trades":      "binance",
    "backfill_binance_open_interest":   "binance",
    "backfill_binance_long_short_ratios":"binance",
    "backfill_binance_funding_rate":    "binance",
    "backfill_binance_book_depth":      "binance",

    "backfill_evm_erc20_transfers":     "transfers",
    "backfill_evm_native_transfers":    "transfers",
    "backfill_btc_transfers":           "transfers",
    "backfill_tron_native_transfers":   "transfers",
    "backfill_tron_trc20_transfers":    "transfers",

    "backfill_aave_v3_events":          "aave",
    "backfill_aave_v2_events":          "aave",
    "backfill_aave_v4_events":          "aave",
    "backfill_aave_events":             "aave",  # legacy alias

    "backfill_uniswap_v3_events":       "uniswap",
    "backfill_uniswap_v2_events":       "uniswap",
    "backfill_uniswap_v4_events":       "uniswap",
    "backfill_uniswap_events":          "uniswap",  # legacy alias

    "backfill_aero_concentrated_events":"aerodrome",
    "backfill_aero_basic_events":       "aerodrome",
    "backfill_aero_events":             "aerodrome",  # legacy alias

    "backfill_lido_events":             "lido",
    "backfill_morpho_events":           "morpho",
    "backfill_spark_events":            "spark",
    "backfill_gmx_v2_events":           "gmx",
    "backfill_gmx_events":              "gmx",  # legacy alias

    # Added in Phase B — derived-MV backfills.
    "backfill_hl_position_history_mv":  "data_process",
    "backfill_hl_fills_pnl_daily":      "data_process",
    "backfill_exchange_flow_minute":    "data_process",
    "backfill_transfers_rematerialize": "data_process",
    # Unified materializer backfill — single job_type covering all 7
    # derived tables. See data_processor/backfill.py.
    "backfill_data_processor":          "data_process",
}


KNOWN_PROVIDERS: frozenset[str] = frozenset(GROUP_TO_PROVIDER.values())
KNOWN_ROLES: frozenset[str] = frozenset({"live", "backfill", "admin", "monolith"})


def current_provider() -> str | None:
    """Provider this container is responsible for, or None for the legacy
    monolith. Raises ValueError if the env var is set to an unknown name —
    fail fast at startup rather than silently spawning nothing."""
    raw = os.environ.get("INGESTION_PROVIDER", "").strip().lower()
    if not raw:
        return None
    if raw not in KNOWN_PROVIDERS:
        raise ValueError(
            f"INGESTION_PROVIDER={raw!r} is not a known provider. "
            f"Known: {sorted(KNOWN_PROVIDERS)}"
        )
    return raw


def current_role() -> str:
    """Container role. Defaults to 'monolith' so an unset env var
    preserves the legacy single-container behaviour exactly."""
    raw = os.environ.get("INGESTION_ROLE", "").strip().lower()
    if not raw:
        return "monolith"
    if raw not in KNOWN_ROLES:
        raise ValueError(
            f"INGESTION_ROLE={raw!r} is not a known role. "
            f"Known: {sorted(KNOWN_ROLES)}"
        )
    return raw


def streams_owned_by(provider: str | None, *, group_of) -> set[str]:
    """Set of stream names this provider owns.

    `group_of` is a callable `name -> group` (typically
    `lambda name: spec_by_name[name].group`). Decoupled from streams.STREAMS
    to avoid a circular import — supervisor passes it in.

    `provider is None` → monolith mode → match every stream.
    """
    from streams import STREAMS  # local import to avoid cycle at module load

    if provider is None:
        return {s.name for s in STREAMS}
    return {
        s.name for s in STREAMS
        if GROUP_TO_PROVIDER.get(s.group) == provider
    }


def job_types_owned_by(provider: str | None) -> set[str]:
    """Set of backfill job_types this provider owns.

    `provider is None` → monolith mode → match every job_type the manager
    knows about (caller will not actually filter)."""
    if provider is None:
        return set(JOB_TYPE_TO_PROVIDER.keys())
    return {jt for jt, p in JOB_TYPE_TO_PROVIDER.items() if p == provider}


def describe() -> str:
    """One-line summary for the startup log."""
    p = current_provider()
    r = current_role()
    return f"provider={p or '<monolith>'} role={r}"
