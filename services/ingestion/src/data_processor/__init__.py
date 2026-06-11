"""Unified materializer worker. One module, two entry points:

  data_processor.live      — long-running supervisor-managed stream that
                              rebuilds recent partitions of every derived
                              table on a tiered cadence.
  data_processor.backfill  — single-shot subprocess spawned by JobManager
                              for a (materializers, since, until) window.

Both modes share the same rebuild primitive in `rebuild.py`: a staging
table is filled with `INSERT … SELECT … FROM source FINAL GROUP BY …` for
a single partition window, then atomically swapped into the target via
`ALTER TABLE target REPLACE PARTITION … FROM staging`. Source-FINAL
guarantees per-source-row contribution exactly once, REPLACE PARTITION
guarantees the read path sees either the old or the new partition but
never a mid-rebuild hole — together they make the whole pipeline
idempotent under arbitrary backfill replays.

Replaces the push MV cascade (mv_exchange_flow, hl_position_history_*_mv,
hl_fills_*_mv, hl_funding_daily_mv) and the
`data_process.exchange_flow_self_heal` 15-min rebuild loop."""
