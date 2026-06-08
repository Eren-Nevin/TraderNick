# DefiStream Hyperliquid Data Gap Report

**Reporter:** Eren Nevin
**Date:** 2026-06-08
**Feeds checked:**
- `/exchange/hyperliquid/ohlcv` (1m bars)
- `/exchange/hyperliquid/trades`
- `/exchange/hyperliquid/fills`
- `/exchange/hyperliquid/trade_history` (hourly)

**Definition of "gap":** A bucket (minute for ohlcv/trades/fills; hour for trade_history) where the API returned zero or near-zero rows (< 5) across the entire 30-token universe.

**Validation:** Confirmed via direct API queries against `/exchange/hyperliquid/ohlcv` that DefiStream's own response is missing rows for these windows — these are not ingestion-side issues. Example: BTC OHLCV query for `2026-03-30T22:50:00 → 2026-03-31T00:30:00` returns rows up to 22:59, then nothing until 23:59 (single BTC row), then resumes at 00:14 next day. Identical pattern observed in our table.

All times UTC.

## Reproducing the gaps

Each table below also includes the API path that reproduces the gap (window slightly padded on each side for context). Append your API key when calling. All paths assume the DefiStream `/exchange/hyperliquid/*` namespace.

For brevity I show BTC-only queries — the gaps hit every token in the universe simultaneously, but BTC is the cheapest single-token verification.

---

## 2026-02-28
| Feed | Gap |
|---|---|
| ohlcv | 23:00–23:59 (60m) |
| trades | 23:00–23:59 (60m) |
| fills | 23:00–23:59 (60m) |
| trade_history | 23:00–24:00 (1h) |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-02-28T22:50:00&until=2026-03-01T00:30:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-02-28T22:50:00&until=2026-03-01T00:30:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-02-28T22:50:00&until=2026-03-01T00:30:00
/exchange/hyperliquid/trade_history?tokens=BTC&window=1h&since=2026-02-28T20:00:00&until=2026-03-01T02:00:00
```

## 2026-03-24
| Feed | Gap |
|---|---|
| ohlcv | 23:00–23:59 (60m) |
| trades | 23:00–23:58 (59m) |
| fills | 23:00–23:58 (59m) |
| trade_history | no gap |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-03-24T22:50:00&until=2026-03-25T00:30:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-03-24T22:50:00&until=2026-03-25T00:30:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-03-24T22:50:00&until=2026-03-25T00:30:00
```

## 2026-03-30
| Feed | Gap |
|---|---|
| ohlcv | 23:00–23:58 (59m) |
| trades | 23:00–23:58 (59m) |
| fills | 23:00–23:58 (59m) |
| trade_history | no gap |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-03-30T22:50:00&until=2026-03-31T00:30:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-03-30T22:50:00&until=2026-03-31T00:30:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-03-30T22:50:00&until=2026-03-31T00:30:00
```

## 2026-03-31
| Feed | Gap |
|---|---|
| ohlcv | 23:00–23:58 (59m) |
| trades | 23:00–23:58 (59m) |
| fills | 23:00–23:58 (59m) |
| trade_history | no gap |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-03-31T22:50:00&until=2026-04-01T00:30:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-03-31T22:50:00&until=2026-04-01T00:30:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-03-31T22:50:00&until=2026-04-01T00:30:00
```

## 2026-04-05 — **major recurring pattern**
On this day, every clock-hour from 00:00 to 23:59 had its **last ~27–28 minutes missing**. Effectively only the first ~32 minutes of each hour returned data. Total: **~626 minutes (10.4 hours) of missing data** across the day for ohlcv/trades/fills.

| Feed | Total gap | Pattern |
|---|---|---|
| ohlcv | 626m | Last 27–28 min of every hour 00–22, see windows below |
| trades | 621m | Same |
| fills | 617m | Same |
| trade_history | no gap | |

Full window list (ohlcv example, the others are within ±1 minute):
```
00:32-00:59 (28m), 01:32-01:59 (28m), 02:32-02:59 (28m), 03:32-03:59 (28m),
04:32-04:59 (28m), 05:32-05:59 (28m), 06:33-06:59 (27m), 07:33-07:59 (27m),
08:33-08:59 (27m), 09:33-09:59 (27m), 10:33-10:59 (27m), 11:33-11:59 (27m),
12:33-12:59 (27m), 13:33-13:59 (27m), 14:33-14:59 (27m), 15:33-15:59 (27m),
16:33-16:59 (27m), 17:33-17:59 (27m), 18:33-18:59 (27m), 19:33-19:59 (27m),
20:33-20:59 (27m), 21:33-21:59 (27m), 22:34-22:59 (26m)
```

Reproduce (single hour example — pattern repeats every hour 00–22):
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-04-05T10:25:00&until=2026-04-05T11:05:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-04-05T10:25:00&until=2026-04-05T11:05:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-04-05T10:25:00&until=2026-04-05T11:05:00
```

Full-day version (all 23 hourly gaps visible in one response):
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-04-05T00:00:00&until=2026-04-06T00:00:00
```

## 2026-04-19
| Feed | Gap |
|---|---|
| ohlcv | 18:43–18:55 (13m) |
| trades | 18:43–18:55 (13m) |
| fills | 18:43–18:55 (13m) |
| trade_history | no gap |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-04-19T18:35:00&until=2026-04-19T19:05:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-04-19T18:35:00&until=2026-04-19T19:05:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-04-19T18:35:00&until=2026-04-19T19:05:00
```

## 2026-04-20
| Feed | Gap |
|---|---|
| ohlcv | 15:54–15:57 (4m) |
| trades | 15:54–15:56 (3m) |
| fills | 15:54–15:56 (3m) |
| trade_history | no gap |

Note: additionally, 15:47–20:38 on this day showed substantially degraded row counts (~5–9 rows/min vs normal ~25–30), suggesting partial-degradation outside the strict zero-row windows above. Not included in the strict gap totals but worth flagging.

Reproduce (strict gap):
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-04-20T15:45:00&until=2026-04-20T16:05:00
```

Reproduce (degraded ~5h window — partial-degradation is visible in the row counts):
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-04-20T15:40:00&until=2026-04-20T20:45:00
```

## 2026-04-25
| Feed | Gap |
|---|---|
| ohlcv | 07:33–07:42 (10m), 07:51–07:53 (3m) |
| trades | 07:33–07:42 (10m) |
| fills | 07:33–07:42 (10m) |
| trade_history | no gap |

Reproduce:
```
/exchange/hyperliquid/ohlcv?tokens=BTC&window=1m&since=2026-04-25T07:25:00&until=2026-04-25T08:05:00
/exchange/hyperliquid/trades?tokens=BTC&since=2026-04-25T07:25:00&until=2026-04-25T08:05:00
/exchange/hyperliquid/fills?tokens=BTC&since=2026-04-25T07:25:00&until=2026-04-25T08:05:00
```

---

## Summary of patterns observed

1. **UTC midnight straddle pattern (02-28, 03-24, 03-30, 03-31):** Recurring ~59–60 minute outage hitting all minute-cadence feeds simultaneously during the 23:00 UTC hour. trade_history affected on 02-28 only (rest had the hourly snapshot at 23:00 land successfully).

2. **Hourly-recurring tail-of-hour pattern (04-05):** Last ~27 minutes of every hour missing for nearly the entire day. trade_history unaffected (hourly snapshot lands on the hour boundary which is still covered).

3. **Isolated short outages (04-19, 04-20, 04-25):** 3–13 minute single-window gaps in the middle of trading hours; all minute feeds hit simultaneously.

In every case the three minute-cadence feeds (ohlcv/trades/fills) drop together with nearly identical timing, suggesting a shared upstream collection issue rather than per-feed bugs.

## Action requested

Please investigate and, where possible, backfill the missing windows. Once data is restored upstream, we'll re-run our backfill jobs to pick up the new rows. Happy to provide more context, additional days, or per-token verification on request.
