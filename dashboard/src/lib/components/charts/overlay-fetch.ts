// Compound-chart overlay fetcher.
//
// Each compound overlay (one entry in ChartInstance.overlays) needs its own
// {time, value}[] series sourced from whatever kind the user picked. This
// helper mirrors the request shapes the primary chart already issues from
// ChartInstance.svelte — but projects every response down to a single
// `{ time, value }` row stream keyed by the overlay's `seriesKey`.
//
// Only kinds listed in OVERLAY_KIND_SERIES with a non-empty entry are
// supported. Tabular kinds and `pc` cannot be overlaid.

import { queuedFetch } from '$lib/fetch-queue';
import type { Interval } from '$lib/api';
import {
  AAVE_V3_KIND_TO_EVENT, AAVE_V3_NET_KIND_TO_EVENTS,
  AAVE_V2_KIND_TO_EVENT, AAVE_V2_NET_KIND_TO_EVENTS,
  AAVE_V4_KIND_TO_EVENT, AAVE_V4_NET_KIND_TO_EVENTS,
  MORPHO_KIND_TO_EVENT, MORPHO_NET_KIND_TO_EVENTS,
  SPARK_KIND_TO_EVENT, SPARK_NET_KIND_TO_EVENTS,
  GMX_V2_KIND_TO_EVENT, GMX_V2_NET_KIND_TO_EVENTS, GMX_V2_PRIMARY_FIELD,
  UNISWAP_V2_KIND_TO_EVENT, UNISWAP_V2_NET_KIND_TO_EVENTS,
  UNISWAP_V3_KIND_TO_EVENT, UNISWAP_V3_NET_KIND_TO_EVENTS,
  UNISWAP_V4_KIND_TO_EVENT, UNISWAP_V4_NET_KIND_TO_EVENTS,
  AERO_CL_KIND_TO_EVENT, AERO_CL_NET_KIND_TO_EVENTS,
  AERO_BASIC_KIND_TO_EVENT, AERO_BASIC_NET_KIND_TO_EVENTS,
  LIDO_KIND_TO_EVENT, LIDO_NET_KIND_TO_EVENTS,
  HL_KIND_TO_EVENT, HL_PRIMARY_FIELD,
  isAaveV2Kind, isAaveV3Kind, isAaveV4Kind, isMorphoKind, isSparkKind,
  isGmxV2Kind, isUniswapV2Kind, isUniswapV3Kind, isUniswapV4Kind,
  isAeroClKind, isAeroBasicKind, isLidoKind, isHlKind,
  maArray,
  type ChartOverlay
} from './config';

export type OverlayPoint = { time: number; value: number };

/** Fetch and project an overlay's data into {time, value}[]. Reads at
 *  `interval` over [since, until]. When the overlay carries an `ma`
 *  config, the values are replaced by their moving-average series before
 *  return (the raw line is dropped — pick MA mode means MA-only). */
export async function fetchOverlayData(
  overlay: ChartOverlay,
  interval: Interval,
  since: Date,
  until: Date,
  signal?: AbortSignal
): Promise<OverlayPoint[]> {
  const sinceIso = since.toISOString();
  const untilIso = until.toISOString();
  const raw = await fetchRawSeries(overlay, interval, sinceIso, untilIso, signal);
  // Apply MA last so the line is smooth.
  if (overlay.ma && raw.length > 0) {
    const vals = raw.map((p) => p.value);
    const smoothed = maArray(vals, overlay.ma.length, overlay.ma.type);
    return raw.map((p, i) => ({ time: p.time, value: smoothed[i] }));
  }
  return raw;
}

async function fetchRawSeries(
  o: ChartOverlay,
  interval: Interval,
  sinceIso: string,
  untilIso: string,
  signal?: AbortSignal
): Promise<OverlayPoint[]> {
  const kind = o.kind;

  // ── Exchange / derivatives ──────────────────────────────────────────
  if (kind === 'ohlcv') {
    const qs = new URLSearchParams({
      token: o.token ?? 'BTC',
      interval,
      since: sinceIso,
      until: untilIso,
      limit: '5000',
      exchange: o.exchange ?? 'binance'
    });
    const res = await queuedFetch(`/api/ohlcv?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay ohlcv ${res.status}`);
    const body = await res.json();
    const candles = (body.candles ?? []) as Array<Record<string, number>>;
    return candles.map((c) => ({ time: c.time, value: numAt(c, o.seriesKey, 'close') }));
  }

  if (kind === 'hl_smart_oi') {
    // Same response shape as /oi_split; the leaderboard knobs ride as
    // extra query params from the overlay's own persisted fields.
    const qs = new URLSearchParams({
      token: o.token ?? 'BTC', interval, since: sinceIso, until: untilIso, limit: '5000',
      pnl_lookback_days: String(o.smartPnlLookbackDays ?? 7),
      pnl_floor_usd:     String(o.smartPnlFloorUsd ?? 10000),
      top_n:             String(o.smartPnlTopN ?? 50),
      leaderboard_scope: o.smartLeaderboardScope ?? 'global',
      pnl_filter:        o.smartPnlFilter ?? 'realized'
    });
    const res = await queuedFetch(`/api/hyperliquid/smart_oi?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay smart_oi ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    if (o.seriesKey === 'long_to_short_oi') {
      return rows.map((r) => {
        const s = Number(r.short_oi_value ?? 0);
        const l = Number(r.long_oi_value ?? 0);
        return { time: r.time, value: s > 0 ? l / s : 0 };
      });
    }
    if (o.seriesKey === 'net_oi_pct') {
      return rows.map((r) => {
        const t = Number(r.total_oi_value ?? 0);
        if (!(t > 0)) return { time: r.time, value: 0 };
        return { time: r.time,
          value: (Number(r.long_oi_value ?? 0) - Number(r.short_oi_value ?? 0)) / t };
      });
    }
    const hlKeys = ['long_oi_value', 'short_oi_value', 'total_oi_value',
                    'long_oi', 'short_oi', 'total_oi'];
    const key = hlKeys.includes(o.seriesKey) ? o.seriesKey : 'total_oi_value';
    return rows.map((r) => ({ time: r.time, value: Number(r[key] ?? 0) }));
  }

  if (kind === 'oi') {
    const exchange = o.exchange ?? 'binance';
    if (exchange === 'hl') {
      const qs = new URLSearchParams({ token: o.token ?? 'BTC', interval, since: sinceIso, until: untilIso, limit: '5000' });
      const res = await queuedFetch(`/api/hyperliquid/oi_split?${qs}`, { signal });
      if (!res.ok) throw new Error(`overlay oi ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      if (o.seriesKey === 'long_to_short_oi') {
        // Unitless ratio. Guard zero-short buckets (early-history HL markets
        // where one side hadn't traded yet) — emit 0 instead of Infinity.
        return rows.map((r) => {
          const s = Number(r.short_oi_value ?? 0);
          const l = Number(r.long_oi_value ?? 0);
          return { time: r.time, value: s > 0 ? l / s : 0 };
        });
      }
      if (o.seriesKey === 'net_oi_pct') {
        // (long - short) / total, in [-1, 1]. Unitless: the mark price
        // cancels, so the value is the same in token or USD space.
        return rows.map((r) => {
          const t = Number(r.total_oi_value ?? 0);
          if (!(t > 0)) return { time: r.time, value: 0 };
          return { time: r.time,
            value: (Number(r.long_oi_value ?? 0) - Number(r.short_oi_value ?? 0)) / t };
        });
      }
      // HL response carries both unit shapes: `*_oi` (token) and `*_oi_value`
      // ($). The seriesKey already names the exact field — just read it.
      const hlKeys = ['long_oi_value', 'short_oi_value', 'total_oi_value',
                      'long_oi', 'short_oi', 'total_oi'];
      const key = hlKeys.includes(o.seriesKey) ? o.seriesKey : 'total_oi_value';
      return rows.map((r) => ({ time: r.time, value: Number(r[key] ?? 0) }));
    }
    const qs = new URLSearchParams({ token: o.token ?? 'BTC', interval, since: sinceIso, until: untilIso, limit: '5000' });
    const res = await queuedFetch(`/api/open_interest?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay oi ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    // Binance OI: server emits `open_interest` (token) and `open_interest_value`
    // ($). Token-amount overlay keys (total_oi/long_oi/short_oi) map to the
    // token field; USD keys (total_oi_value) map to the dollar field. Binance
    // has no long/short split — long/short selections fall through to total.
    const wantsToken = o.seriesKey === 'total_oi'
      || o.seriesKey === 'long_oi' || o.seriesKey === 'short_oi';
    return rows.map((r) => ({
      time: r.time,
      value: Number(wantsToken
        ? (r.open_interest ?? 0)
        : (r.open_interest_value ?? 0))
    }));
  }

  if (kind === 'fr') {
    const qs = new URLSearchParams({
      token: o.token ?? 'BTC',
      interval,
      since: sinceIso,
      until: untilIso,
      limit: '5000',
      exchange: o.exchange ?? 'binance'
    });
    const res = await queuedFetch(`/api/funding_rate?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay fr ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    // The server emits `rate` (raw per-event decimal — Binance per-8h,
    // HL per-1h). The dashboard's primary FR chart normalizes client-
    // side; the overlay has to do the same or every bucket reads as 0
    // (the bps/APR field never exists on the response).
    const hoursPerEvent = (o.exchange ?? 'binance') === 'hl' ? 1 : 8;
    const isApr = (o.frDisplay ?? 'rate8h') === 'apr';
    const factor = isApr
      ? (24 / hoursPerEvent) * 365 * 100      // → annualized %
      : (8 / hoursPerEvent) * 10000;          // → bps per 8h (Coinglass)
    return rows.map((r) => ({ time: r.time, value: Number(r.rate ?? 0) * factor }));
  }

  if (kind === 'ls' || kind === 'tt') {
    const qs = new URLSearchParams({
      token: o.token ?? 'BTC',
      interval,
      since: sinceIso,
      until: untilIso,
      limit: '5000',
      exchange: o.exchange ?? 'binance'
    });
    const res = await queuedFetch(`/api/long_short_ratios?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay ${kind} ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
  }

  if (kind === 'bs' || kind === 'sz') {
    const qs = new URLSearchParams({
      token: o.token ?? 'BTC',
      interval,
      since: sinceIso,
      until: untilIso,
      limit: '5000',
      exchange: o.exchange ?? 'binance',
      under: String(o.under ?? 10000),
      over: String(o.over ?? 100000)
    });
    const res = await queuedFetch(`/api/trade_volume?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay ${kind} ${res.status}`);
    const body = await res.json();
    const rows = (body.buckets ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
  }

  // ── Flows ────────────────────────────────────────────────────────────
  if (kind === 'transfer') {
    const qs = new URLSearchParams({
      interval,
      since: sinceIso,
      until: untilIso,
      limit: '5000'
    });
    if (o.chainGroup) qs.set('chain_group', o.chainGroup);
    else qs.set('chain', o.chain ?? 'ETH');
    if (o.tokenGroup) qs.set('token_group', o.tokenGroup);
    else qs.set('token', o.token ?? 'USDC');
    const res = await queuedFetch(`/api/transfers/aggregate?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay transfer ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
  }

  if (kind === 'exchange_flow') {
    const ex = o.exchangeFlowExchange ?? 'binance';
    const buildQS = (direction: 'in' | 'out') => {
      const qs = new URLSearchParams({
        direction, exchange: ex,
        interval, since: sinceIso, until: untilIso, limit: '10000'
      });
      // HL CeX flows are pinned to ARB/USDC on chain — chain_group/
      // token_group don't apply there. Other exchanges may use either
      // the singleton chain or a server-side bundle (e.g. EVM).
      if (ex === 'hyperliquid') {
        qs.set('chain', 'ARB');
      } else if (o.chainGroup) {
        qs.set('chain_group', o.chainGroup);
      } else {
        qs.set('chain', o.chain ?? 'ETH');
      }
      // Server-side compound bundle (e.g. USDC+USDT) when present;
      // otherwise the single token. Hyperliquid CeX flows are USDC-only,
      // so the token_group path doesn't apply there even if set.
      if (o.tokenGroup && ex !== 'hyperliquid') qs.set('token_group', o.tokenGroup);
      else qs.set('token', o.token ?? 'USDC');
      return qs;
    };
    // Only fetch the side(s) we need.
    if (o.seriesKey === 'inflow') {
      const res = await queuedFetch(`/api/exchange_flow/aggregate?${buildQS('in')}`, { signal });
      if (!res.ok) throw new Error(`overlay exchange_flow ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r.sum_value_usd ?? 0) }));
    }
    if (o.seriesKey === 'outflow') {
      const res = await queuedFetch(`/api/exchange_flow/aggregate?${buildQS('out')}`, { signal });
      if (!res.ok) throw new Error(`overlay exchange_flow ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r.sum_value_usd ?? 0) }));
    }
    // netflow: fetch both, subtract per bucket.
    const [inRes, outRes] = await Promise.all([
      queuedFetch(`/api/exchange_flow/aggregate?${buildQS('in')}`, { signal }),
      queuedFetch(`/api/exchange_flow/aggregate?${buildQS('out')}`, { signal })
    ]);
    if (!inRes.ok || !outRes.ok) throw new Error(`overlay exchange_flow ${inRes.status}/${outRes.status}`);
    const inBody = await inRes.json();
    const outBody = await outRes.json();
    const outByTime = new Map<number, number>();
    for (const r of (outBody.series ?? []) as Array<Record<string, number>>) {
      outByTime.set(r.time, Number(r.sum_value_usd ?? 0));
    }
    const out: OverlayPoint[] = [];
    const seen = new Set<number>();
    for (const r of (inBody.series ?? []) as Array<Record<string, number>>) {
      out.push({ time: r.time, value: Number(r.sum_value_usd ?? 0) - (outByTime.get(r.time) ?? 0) });
      seen.add(r.time);
    }
    for (const r of (outBody.series ?? []) as Array<Record<string, number>>) {
      if (seen.has(r.time)) continue;
      out.push({ time: r.time, value: -Number(r.sum_value_usd ?? 0) });
    }
    out.sort((a, b) => a.time - b.time);
    return out;
  }

  // ── Lending: AAVE V2 / V3 / V4 ──────────────────────────────────────
  if (isAaveV2Kind(kind) || isAaveV3Kind(kind) || isAaveV4Kind(kind)) {
    const family =
      isAaveV2Kind(kind) ? { ev: AAVE_V2_KIND_TO_EVENT, net: AAVE_V2_NET_KIND_TO_EVENTS, ep: '/api/aave_v2/aggregate' }
      : isAaveV4Kind(kind) ? { ev: AAVE_V4_KIND_TO_EVENT, net: AAVE_V4_NET_KIND_TO_EVENTS, ep: '/api/aave_v4/aggregate' }
      : { ev: AAVE_V3_KIND_TO_EVENT, net: AAVE_V3_NET_KIND_TO_EVENTS, ep: '/api/aave/aggregate' };
    return aggregateEventOrNet(o, family.ev, family.net, family.ep, interval, sinceIso, untilIso, signal);
  }
  if (isMorphoKind(kind)) {
    return aggregateEventOrNet(o, MORPHO_KIND_TO_EVENT, MORPHO_NET_KIND_TO_EVENTS, '/api/morpho/aggregate', interval, sinceIso, untilIso, signal);
  }
  if (isSparkKind(kind)) {
    return aggregateEventOrNet(o, SPARK_KIND_TO_EVENT, SPARK_NET_KIND_TO_EVENTS, '/api/spark/aggregate', interval, sinceIso, untilIso, signal);
  }

  // ── Lido ────────────────────────────────────────────────────────────
  if (isLidoKind(kind)) {
    const ev = LIDO_KIND_TO_EVENT[kind];
    const netEvs = LIDO_NET_KIND_TO_EVENTS[kind];
    const buildQS = (event: string) => {
      const qs = new URLSearchParams({
        event, interval, since: sinceIso, until: untilIso, limit: '5000'
      });
      if (o.chainGroup) qs.set('chain_group', o.chainGroup);
      else qs.set('chain', o.chain ?? 'ETH');
      return qs;
    };
    if (netEvs) {
      const [posEv, negEv] = netEvs;
      const [posRes, negRes] = await Promise.all([
        queuedFetch(`/api/lido/aggregate?${buildQS(posEv)}`, { signal }),
        queuedFetch(`/api/lido/aggregate?${buildQS(negEv)}`, { signal })
      ]);
      if (!posRes.ok || !negRes.ok) throw new Error(`overlay lido ${posRes.status}/${negRes.status}`);
      return netSubtractByTime(await posRes.json(), await negRes.json(), o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/lido/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay lido ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
    }
  }

  // ── DeX: Uniswap V2 / V3 / V4 ───────────────────────────────────────
  if (isUniswapV2Kind(kind)) {
    const pool = o.uniPool;
    if (!pool) return [];
    const buildQS = (event: string) => new URLSearchParams({
      event, chain: o.chain ?? 'ETH',
      symbol0: pool.symbol0, symbol1: pool.symbol1,
      interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    const ev = UNISWAP_V2_KIND_TO_EVENT[kind];
    const netEvs = UNISWAP_V2_NET_KIND_TO_EVENTS[kind];
    if (netEvs) {
      const [pe, ne] = netEvs;
      const [a, b] = await Promise.all([
        queuedFetch(`/api/uniswap_v2/aggregate?${buildQS(pe)}`, { signal }),
        queuedFetch(`/api/uniswap_v2/aggregate?${buildQS(ne)}`, { signal })
      ]);
      if (!a.ok || !b.ok) throw new Error(`overlay uniswap_v2 ${a.status}/${b.status}`);
      return netSubtractByTime(await a.json(), await b.json(), o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/uniswap_v2/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay uniswap_v2 ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
    }
  }
  if (isUniswapV3Kind(kind)) {
    const pool = o.uniPool;
    if (!pool) return [];
    const buildQS = (event: string) => new URLSearchParams({
      event, chain: o.chain ?? 'ETH',
      symbol0: pool.symbol0, symbol1: pool.symbol1, fee_tier: String(pool.fee),
      interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    if (kind === 'uniswap_v3_net_swap_flow') {
      const res = await queuedFetch(`/api/uniswap/aggregate?${buildQS('swap')}`, { signal });
      if (!res.ok) throw new Error(`overlay uniswap_v3_net_swap_flow ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r.sum_value_usd_t0t1 ?? 0) - Number(r.sum_value_usd_t1t0 ?? 0) }));
    }
    const ev = UNISWAP_V3_KIND_TO_EVENT[kind];
    const netEvs = UNISWAP_V3_NET_KIND_TO_EVENTS[kind];
    if (netEvs) {
      const [pe, ne] = netEvs;
      const [a, b] = await Promise.all([
        queuedFetch(`/api/uniswap/aggregate?${buildQS(pe)}`, { signal }),
        queuedFetch(`/api/uniswap/aggregate?${buildQS(ne)}`, { signal })
      ]);
      if (!a.ok || !b.ok) throw new Error(`overlay uniswap_v3 ${a.status}/${b.status}`);
      return netSubtractByTime(await a.json(), await b.json(), o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/uniswap/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay uniswap_v3 ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
    }
  }
  if (isUniswapV4Kind(kind)) {
    const pool = o.uniV4Pool;
    if (!pool) return [];
    const buildQS = (event: string) => new URLSearchParams({
      event, chain: o.chain ?? 'ETH',
      symbol0: pool.symbol0, symbol1: pool.symbol1,
      fee: String(pool.fee), tick_spacing: String(pool.tick_spacing), hooks: pool.hooks,
      interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    const ev = UNISWAP_V4_KIND_TO_EVENT[kind];
    const netEvs = UNISWAP_V4_NET_KIND_TO_EVENTS[kind];
    if (netEvs) {
      const [pe, ne] = netEvs;
      const [a, b] = await Promise.all([
        queuedFetch(`/api/uniswap_v4/aggregate?${buildQS(pe)}`, { signal }),
        queuedFetch(`/api/uniswap_v4/aggregate?${buildQS(ne)}`, { signal })
      ]);
      if (!a.ok || !b.ok) throw new Error(`overlay uniswap_v4 ${a.status}/${b.status}`);
      return netSubtractByTime(await a.json(), await b.json(), o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/uniswap_v4/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay uniswap_v4 ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
    }
  }

  // ── DeX: Aerodrome CL / Basic ───────────────────────────────────────
  if (isAeroClKind(kind)) {
    const pool = o.aeroPool;
    if (!pool) return [];
    const buildQS = (event: string) => new URLSearchParams({
      event, chain: 'BASE',
      symbol0: pool.symbol0, symbol1: pool.symbol1, tick_spacing: String(pool.tick_spacing),
      interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    const ev = AERO_CL_KIND_TO_EVENT[kind];
    const netEvs = AERO_CL_NET_KIND_TO_EVENTS[kind];
    if (netEvs) {
      const [pe, ne] = netEvs;
      const [a, b] = await Promise.all([
        queuedFetch(`/api/aero/aggregate?${buildQS(pe)}`, { signal }),
        queuedFetch(`/api/aero/aggregate?${buildQS(ne)}`, { signal })
      ]);
      if (!a.ok || !b.ok) throw new Error(`overlay aero_cl ${a.status}/${b.status}`);
      return netSubtractByTime(await a.json(), await b.json(), o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/aero/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay aero_cl ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
    }
  }
  if (isAeroBasicKind(kind)) {
    const pool = o.aeroBasicPool;
    if (!pool) return [];
    const buildQS = (event: string) => new URLSearchParams({
      event, chain: 'BASE',
      symbol0: pool.symbol0, symbol1: pool.symbol1, stable: pool.stable ? '1' : '0',
      interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    const ev = AERO_BASIC_KIND_TO_EVENT[kind];
    const netEvs = AERO_BASIC_NET_KIND_TO_EVENTS[kind];
    if (netEvs) {
      const [pe, ne] = netEvs;
      const [a, b] = await Promise.all([
        queuedFetch(`/api/aero_basic/aggregate?${buildQS(pe)}`, { signal }),
        queuedFetch(`/api/aero_basic/aggregate?${buildQS(ne)}`, { signal })
      ]);
      if (!a.ok || !b.ok) throw new Error(`overlay aero_basic ${a.status}/${b.status}`);
      return netSubtractByTime(await a.json(), await b.json(), o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/aero_basic/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay aero_basic ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
    }
  }

  // ── Perp: GMX V2 ─────────────────────────────────────────────────────
  if (isGmxV2Kind(kind)) {
    const buildQS = (event: string) => {
      const qs = new URLSearchParams({
        event, chain: o.chain ?? 'ARB', interval, since: sinceIso, until: untilIso, limit: '5000'
      });
      if (o.gmxMarket && o.gmxMarket.length > 0) qs.set('market', o.gmxMarket);
      return qs;
    };
    const primary = GMX_V2_PRIMARY_FIELD[kind] ?? 'sum_value_usd';
    const ev = GMX_V2_KIND_TO_EVENT[kind];
    const netEvs = GMX_V2_NET_KIND_TO_EVENTS[kind];
    if (netEvs) {
      const [pe, ne] = netEvs;
      const [a, b] = await Promise.all([
        queuedFetch(`/api/gmx/aggregate?${buildQS(pe)}`, { signal }),
        queuedFetch(`/api/gmx/aggregate?${buildQS(ne)}`, { signal })
      ]);
      if (!a.ok || !b.ok) throw new Error(`overlay gmx ${a.status}/${b.status}`);
      return netSubtractByTime(await a.json(), await b.json(), o.seriesKey === 'sum_amount' ? primary : o.seriesKey);
    }
    if (ev) {
      const res = await queuedFetch(`/api/gmx/aggregate?${buildQS(ev)}`, { signal });
      if (!res.ok) throw new Error(`overlay gmx ${res.status}`);
      const body = await res.json();
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      const field = o.seriesKey === 'sum_amount' ? primary : o.seriesKey;
      return rows.map((r) => ({ time: r.time, value: Number(r[field] ?? 0) }));
    }
  }

  // ── Perp: Hyperliquid ────────────────────────────────────────────────
  if (kind === 'hl_transfers') {
    const qs = new URLSearchParams({ interval, since: sinceIso, until: untilIso, limit: '5000' });
    const res = await queuedFetch(`/api/hyperliquid/bridge_flows?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay hl_transfers ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
  }
  if (kind === 'hl_vault_net') {
    const qs = new URLSearchParams({ interval, since: sinceIso, until: untilIso, limit: '5000' });
    const res = await queuedFetch(`/api/hyperliquid/vault_flow?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay hl_vault_net ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
  }
  if (kind === 'hl_unrealized_pnl') {
    const qs = new URLSearchParams({
      token: o.token ?? 'BTC', interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    if (o.hlWallet && o.hlWallet.length > 0) qs.set('wallet', o.hlWallet);
    const res = await queuedFetch(`/api/hyperliquid/unrealized_pnl?${qs}`, { signal });
    if (!res.ok) throw new Error(`overlay hl_unrealized_pnl ${res.status}`);
    const body = await res.json();
    const rows = (body.series ?? []) as Array<Record<string, number>>;
    return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
  }
  if (isHlKind(kind)) {
    const event = HL_KIND_TO_EVENT[kind];
    if (event) {
      const qs = new URLSearchParams({
        event, token: o.token ?? 'BTC', interval, since: sinceIso, until: untilIso, limit: '5000'
      });
      if (o.hlWallet && o.hlWallet.length > 0) qs.set('wallet', o.hlWallet);
      else if (o.hlWalletCategory && o.hlWalletCategory.length > 0) qs.set('wallet_category', o.hlWalletCategory);
      const res = await queuedFetch(`/api/hyperliquid/aggregate?${qs}`, { signal });
      if (!res.ok) throw new Error(`overlay hl ${res.status}`);
      const body = await res.json();
      const primary = HL_PRIMARY_FIELD[kind] ?? 'sum_value_usd';
      const field = o.seriesKey === 'sum_amount' ? primary : o.seriesKey;
      const rows = (body.series ?? []) as Array<Record<string, number>>;
      return rows.map((r) => ({ time: r.time, value: Number(r[field] ?? r[primary] ?? 0) }));
    }
  }

  // Unsupported kind for overlays.
  return [];
}

/** Shared fetch path for chain+token event/net families (AAVE V2/V3/V4,
 *  Morpho, Spark). One event = one fetch; net kinds fetch two and subtract
 *  the seriesKey field per bucket. */
async function aggregateEventOrNet(
  o: ChartOverlay,
  evMap: Partial<Record<string, string>>,
  netMap: Partial<Record<string, [string, string]>>,
  endpoint: string,
  interval: Interval,
  sinceIso: string,
  untilIso: string,
  signal?: AbortSignal
): Promise<OverlayPoint[]> {
  const buildQS = (event: string) => {
    const qs = new URLSearchParams({
      event, interval, since: sinceIso, until: untilIso, limit: '5000'
    });
    if (o.chainGroup) qs.set('chain_group', o.chainGroup);
    else qs.set('chain', o.chain ?? 'ETH');
    if (o.tokenGroup) qs.set('token_group', o.tokenGroup);
    else qs.set('token', o.token ?? 'USDC');
    return qs;
  };
  const netEvs = netMap[o.kind];
  if (netEvs) {
    const [pe, ne] = netEvs;
    const [a, b] = await Promise.all([
      queuedFetch(`${endpoint}?${buildQS(pe)}`, { signal }),
      queuedFetch(`${endpoint}?${buildQS(ne)}`, { signal })
    ]);
    if (!a.ok || !b.ok) throw new Error(`overlay ${o.kind} ${a.status}/${b.status}`);
    return netSubtractByTime(await a.json(), await b.json(), o.seriesKey);
  }
  const ev = evMap[o.kind];
  if (!ev) return [];
  const res = await queuedFetch(`${endpoint}?${buildQS(ev)}`, { signal });
  if (!res.ok) throw new Error(`overlay ${o.kind} ${res.status}`);
  const body = await res.json();
  const rows = (body.series ?? []) as Array<Record<string, number>>;
  return rows.map((r) => ({ time: r.time, value: Number(r[o.seriesKey] ?? 0) }));
}

/** Compute pos - neg per bucket on a chosen field; emit missing-bucket
 *  values as ±field. Returns sorted timeline. */
function netSubtractByTime(
  posBody: { series?: Array<Record<string, number>> },
  negBody: { series?: Array<Record<string, number>> },
  field: string
): OverlayPoint[] {
  const negByTime = new Map<number, number>();
  for (const r of (negBody.series ?? [])) {
    negByTime.set(r.time, Number(r[field] ?? 0));
  }
  const out: OverlayPoint[] = [];
  const seen = new Set<number>();
  for (const r of (posBody.series ?? [])) {
    out.push({ time: r.time, value: Number(r[field] ?? 0) - (negByTime.get(r.time) ?? 0) });
    seen.add(r.time);
  }
  for (const r of (negBody.series ?? [])) {
    if (seen.has(r.time)) continue;
    out.push({ time: r.time, value: -Number(r[field] ?? 0) });
  }
  out.sort((a, b) => a.time - b.time);
  return out;
}

function numAt(obj: Record<string, number>, key: string, fallback: string): number {
  const v = obj[key];
  if (typeof v === 'number') return v;
  const fb = obj[fallback];
  return typeof fb === 'number' ? fb : 0;
}
