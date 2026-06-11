# DefiStream Hyperliquid Data Gap Report — June 2026 follow-up

**Reporter:** Eren Nevin
**Date:** 2026-06-10
**Follow-up to:** `defistream_hl_gaps_report.md` (reported 2026-06-08, covered Feb–Apr 2026 gaps)

**Feeds checked:**
- `/exchange/hyperliquid/ohlcv` (1m bars)
- `/exchange/hyperliquid/trades`
- `/exchange/hyperliquid/fills`

**Definition of "gap":** A minute bucket where the API returned zero or near-zero rows across the entire 30-token roster.

**Validation:** Confirmed on our side that the missing minutes are synchronized across ALL 30 tokens AND across all three feeds (ohlcv, trades, fills) for the same instants — the only explanation is an upstream blackout, not per-token ingestion issues. Verified on `2026-06-05 16:00` UTC: ohlcv, trades, and fills all stop at 16:30 and resume at 16:52 (21 minutes missing across all three feeds).

All times UTC.

---

## Summary

5 days in early June 2026 show the same synchronized-blackout pattern previously reported. **8 affected hours total**, each with **15–30 minutes of missing data** spread across the hour.

| date | affected hours | total missing |
|---|---|---|
| 2026-06-02 | 21:00 | ~26 min |
| 2026-06-03 | 14:00 | ~19 min |
| 2026-06-04 | 11:00, 15:00 | ~17 + ~22 min |
| 2026-06-05 | 13:00, 16:00, 21:00 | ~23 + ~25 + ~18 min |
| 2026-06-09 | 15:00 | ~24 min |

---

## 2026-06-02
| Feed | Missing minutes within 21:00 UTC |
|---|---|
| ohlcv  | 21:13, 21:15–21:24, 21:28–21:32, 21:35–21:39, 21:43–21:44 (+ partial coverage at 21:12, 21:14, 21:25, 21:42) |
| trades | same pattern, same minutes |
| fills  | same pattern, same minutes |

Reproduce (BTC-only — gaps hit every token):
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-02T20:50:00&until=2026-06-02T22:10:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-06-02T20:50:00&until=2026-06-02T22:10:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-06-02T20:50:00&until=2026-06-02T22:10:00
```

## 2026-06-03
| Feed | Missing minutes within 14:00 UTC |
|---|---|
| ohlcv  | 14:25–14:42, 14:49 (+ partial 14:43) |
| trades | same |
| fills  | same |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-03T13:50:00&until=2026-06-03T15:10:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-06-03T13:50:00&until=2026-06-03T15:10:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-06-03T13:50:00&until=2026-06-03T15:10:00
```

## 2026-06-04
| Feed | Missing minutes within 11:00 UTC |
|---|---|
| ohlcv  | 11:00–11:07 (start of hour), 11:34–11:41 (+ partial 11:33, 11:42, 11:45) |
| trades | same |
| fills  | same |

| Feed | Missing minutes within 15:00 UTC |
|---|---|
| ohlcv  | 15:14–15:29, 15:47–15:48, 15:53–15:55 (+ partial 15:13, 15:30, 15:49) |
| trades | same |
| fills  | same |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-04T10:50:00&until=2026-06-04T12:10:00
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-04T14:50:00&until=2026-06-04T16:10:00
```
(trades/fills follow same window)

## 2026-06-05
| Feed | Missing minutes within 13:00 UTC |
|---|---|
| ohlcv  | 13:22–13:26, 13:43–13:59 (end of hour) (+ partial 13:21, 13:27) |
| trades | same |
| fills  | same |

| Feed | Missing minutes within 16:00 UTC |
|---|---|
| ohlcv  | 16:04–16:06, 16:31–16:51, 16:59 (+ partial 16:03) |
| trades | same |
| fills  | same |

| Feed | Missing minutes within 21:00 UTC |
|---|---|
| ohlcv  | 21:05–21:14, 21:23–21:25, 21:29–21:31 (+ partial 21:15, 21:26, 21:32) |
| trades | same |
| fills  | same |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-05T12:50:00&until=2026-06-05T14:10:00
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-05T15:50:00&until=2026-06-05T17:10:00
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-05T20:50:00&until=2026-06-05T22:10:00
```

## 2026-06-09
| Feed | Missing minutes within 15:00 UTC |
|---|---|
| ohlcv  | 15:38–15:59 (end of hour) (+ partial 15:25, 15:26) |
| trades | same |
| fills  | same |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-06-09T14:50:00&until=2026-06-09T16:10:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-06-09T14:50:00&until=2026-06-09T16:10:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-06-09T14:50:00&until=2026-06-09T16:10:00
```

---

## Observations vs the prior report

1. **Same upstream-blackout signature.** Synchronized across all 30 tokens AND across ohlcv/trades/fills feeds for the same minute instants. Distinct from the per-token sweep patterns we've seen in other DS endpoints.

2. **Higher gap density per affected hour.** Where the Feb–Apr report typically showed one continuous ~28-minute trailing-half-of-hour blackout, the June dates show several shorter blocks scattered through the hour (e.g. `2026-06-02 21:00` has at least 4 separate missing-minute clusters within one hour).

3. **Affected hours are scattered, not clustered at top/bottom of day.** Unlike `2026-04-05` (which showed a regular "last ~27 min of every hour" pattern), the June gaps land on random hours — 11, 13, 14, 15, 16, 21. No obvious time-of-day correlation we can detect from our side.

4. **`trade_history` not checked here.** The Feb–Apr report noted `trade_history` was typically gap-free on the same days. We haven't drilled into trade_history for June; happy to add if useful.

## Request

Please investigate and re-index these hours when possible. Once re-indexed, we'll re-run our ingestion backfill to pick them up.

If the dates above overlap with periods already known to your team, treat this as additional evidence of the same incident rather than independent new reports.
