<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import LineChart from '$lib/components/LineChart.svelte';
  import SignedBarChart from '$lib/components/SignedBarChart.svelte';
  import ChartPanel from '$lib/components/ChartPanel.svelte';
  import {
    INTERVALS,
    type Candle,
    type FundingRateRow,
    type Interval,
    type LongShortRow,
    type OpenInterestRow,
    type VolumeBucket
  } from '$lib/api';
  import type { PageData } from './$types';

  function fmtUsdAxis(v: number) {
    const abs = Math.abs(v);
    if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  }
  function fmtUsdTooltip(v: number) {
    const abs = Math.abs(v);
    if (abs >= 1e9) return `$${(v / 1e9).toFixed(3)}B`;
    if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(2)}`;
  }

  let { data }: { data: PageData } = $props();

  type View = [number, number] | null;
  type MAType = 'sma' | 'ema' | 'wma';
  type ChartId = 'ohlcv' | 'bs' | 'sz' | 'oi' | 'tt' | 'ls' | 'fr';

  let loading = $state(false);
  let error = $state<string | null>(null);

  let syncZoom = $state(true);
  let sharedView = $state<View>(null);
  let sharedHoverTime = $state<number | null>(null);

  const LOOKBACK_DAYS: Record<Interval, number> = {
    '1m': 1,
    '5m': 3,
    '15m': 7,
    '30m': 14,
    '1h': 14,
    '4h': 30,
    '1d': 30
  };

  function lookbackWindow(iv: Interval): { since: Date; until: Date } {
    const now = new Date();
    const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
    const since = new Date(until.getTime() - LOOKBACK_DAYS[iv] * 24 * 60 * 60 * 1000);
    return { since, until };
  }

  function unix(iso: string): number {
    return Math.floor(new Date(iso).getTime() / 1000);
  }

  function smaArray(vals: number[], n: number): number[] {
    const out = new Array<number>(vals.length);
    let sum = 0;
    for (let i = 0; i < vals.length; i++) {
      sum += vals[i];
      if (i >= n) sum -= vals[i - n];
      out[i] = i >= n - 1 ? sum / n : sum / (i + 1);
    }
    return out;
  }
  function emaArray(vals: number[], n: number): number[] {
    const out = new Array<number>(vals.length);
    if (!vals.length) return out;
    const alpha = 2 / (n + 1);
    let ema = vals[0];
    out[0] = ema;
    for (let i = 1; i < vals.length; i++) {
      ema = alpha * vals[i] + (1 - alpha) * ema;
      out[i] = ema;
    }
    return out;
  }
  function wmaArray(vals: number[], n: number): number[] {
    const out = new Array<number>(vals.length);
    for (let i = 0; i < vals.length; i++) {
      const w = Math.min(n, i + 1);
      let num = 0;
      let den = 0;
      for (let k = 0; k < w; k++) {
        const weight = w - k;
        num += vals[i - k] * weight;
        den += weight;
      }
      out[i] = den > 0 ? num / den : 0;
    }
    return out;
  }
  function maArray(vals: number[], n: number, type: MAType): number[] {
    if (type === 'ema') return emaArray(vals, n);
    if (type === 'wma') return wmaArray(vals, n);
    return smaArray(vals, n);
  }

  // -------- OHLCV ----------
  let ohlcvToken = $state(data.token);
  let ohlcvInterval = $state<Interval>(data.interval as Interval);
  let ohlcvCandles = $state<Candle[]>(data.candles);
  let ohlcvSince = $state(data.since);
  let ohlcvUntil = $state(data.until);
  let ohlcvLoadedKey = $state(`${data.token}|${data.interval}`);
  let ohlcvView = $state<View>(null);
  let ohlcvHoverTime = $state<number | null>(null);
  let ohlcvCollapsed = $state(false);
  let pinOHLCV = $state(false);
  let showOHLCVPoint = $state(true);
  let showOHLCVCumulative = $state(false);
  let ohlcvMALength = $state(9);
  let ohlcvMAType = $state<MAType>('sma');

  let ohlcvXExtent = $derived<[number, number]>([unix(ohlcvSince), unix(ohlcvUntil)]);

  $effect(() => {
    const key = `${ohlcvToken}|${ohlcvInterval}`;
    if (key === ohlcvLoadedKey) return;
    void loadOhlcv();
  });

  async function loadOhlcv() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(ohlcvInterval);
      const qs = new URLSearchParams({
        token: ohlcvToken,
        interval: ohlcvInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const res = await fetch(`/api/ohlcv?${qs}`);
      if (!res.ok) throw new Error(`ohlcv ${res.status}`);
      const body = await res.json();
      ohlcvCandles = body.candles ?? [];
      ohlcvSince = since.toISOString();
      ohlcvUntil = until.toISOString();
      ohlcvLoadedKey = `${ohlcvToken}|${ohlcvInterval}`;
      ohlcvView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  let ohlcvCumulativeLines = $derived.by(() => {
    if (ohlcvCandles.length === 0) return [];
    const ma = maArray(
      ohlcvCandles.map((c) => c.close),
      ohlcvMALength,
      ohlcvMAType
    );
    const tag = `${ohlcvMAType.toUpperCase()}(${ohlcvMALength})`;
    return [
      {
        key: 'cum_close',
        label: `Close ${tag}`,
        color: '#fbbf24',
        compute: (_d: Candle, i: number) => ma[i]
      }
    ];
  });
  let ohlcvLines = $derived(showOHLCVCumulative ? ohlcvCumulativeLines : []);

  // -------- Open Interest ----------
  let oiToken = $state(data.token);
  let oiInterval = $state<Interval>(data.interval as Interval);
  let oiData = $state<OpenInterestRow[]>(data.openInterest);
  let oiSince = $state(data.since);
  let oiUntil = $state(data.until);
  let oiLoadedKey = $state(`${data.token}|${data.interval}`);
  let oiView = $state<View>(null);
  let oiHoverTime = $state<number | null>(null);
  let oiCollapsed = $state(false);
  let showOIPoint = $state(true);
  let showOICumulative = $state(false);
  let oiMALength = $state(9);
  let oiMAType = $state<MAType>('sma');

  let oiXExtent = $derived<[number, number]>([unix(oiSince), unix(oiUntil)]);

  $effect(() => {
    const key = `${oiToken}|${oiInterval}`;
    if (key === oiLoadedKey) return;
    void loadOi();
  });

  async function loadOi() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(oiInterval);
      const qs = new URLSearchParams({
        token: oiToken,
        interval: oiInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const res = await fetch(`/api/open_interest?${qs}`);
      if (!res.ok) throw new Error(`open_interest ${res.status}`);
      const body = await res.json();
      oiData = body.series ?? [];
      oiSince = since.toISOString();
      oiUntil = until.toISOString();
      oiLoadedKey = `${oiToken}|${oiInterval}`;
      oiView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const OI_LINES = [
    {
      key: 'oi_usd',
      label: 'OI (USD)',
      color: '#06b6d4',
      compute: (d: OpenInterestRow) => d.open_interest_value
    }
  ];

  let oiCumulativeLines = $derived.by(() => {
    if (oiData.length === 0) return [];
    const ma = maArray(
      oiData.map((d) => d.open_interest_value),
      oiMALength,
      oiMAType
    );
    const tag = `${oiMAType.toUpperCase()}(${oiMALength})`;
    return [
      {
        key: 'cum_oi',
        label: `OI ${tag}`,
        color: '#06b6d4',
        dash: '5,3',
        compute: (_d: OpenInterestRow, i: number) => ma[i]
      }
    ];
  });
  let oiLines = $derived([
    ...(showOIPoint ? OI_LINES : []),
    ...(showOICumulative ? oiCumulativeLines : [])
  ]);

  // -------- Funding Rate ----------
  let frToken = $state(data.token);
  let frInterval = $state<Interval>(data.interval as Interval);
  let frData = $state<FundingRateRow[]>(data.fundingRate);
  let frSince = $state(data.since);
  let frUntil = $state(data.until);
  let frLoadedKey = $state(`${data.token}|${data.interval}`);
  let frView = $state<View>(null);
  let frHoverTime = $state<number | null>(null);
  let frCollapsed = $state(false);
  let showFRPoint = $state(true);
  let showFRCumulative = $state(false);
  let frMALength = $state(9);
  let frMAType = $state<MAType>('sma');

  let frXExtent = $derived<[number, number]>([unix(frSince), unix(frUntil)]);

  $effect(() => {
    const key = `${frToken}|${frInterval}`;
    if (key === frLoadedKey) return;
    void loadFr();
  });

  async function loadFr() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(frInterval);
      const qs = new URLSearchParams({
        token: frToken,
        interval: frInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const res = await fetch(`/api/funding_rate?${qs}`);
      if (!res.ok) throw new Error(`funding_rate ${res.status}`);
      const body = await res.json();
      frData = body.series ?? [];
      frSince = since.toISOString();
      frUntil = until.toISOString();
      frLoadedKey = `${frToken}|${frInterval}`;
      frView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  let frDataBps = $derived(frData.map((d) => ({ ...d, rate_bps: d.rate * 10000 })));

  let frCumulativeLines = $derived.by(() => {
    if (frDataBps.length === 0) return [];
    const ma = maArray(
      frDataBps.map((d) => d.rate_bps),
      frMALength,
      frMAType
    );
    const tag = `${frMAType.toUpperCase()}(${frMALength})`;
    return [
      {
        key: 'cum_fr',
        label: `Rate ${tag}`,
        color: '#fbbf24',
        dash: '5,3',
        compute: (_d: FundingRateRow & { rate_bps: number }, i: number) => ma[i]
      }
    ];
  });
  let frLines = $derived(showFRCumulative ? frCumulativeLines : []);

  // -------- Buyer / Seller Taker Volume ----------
  let bsToken = $state(data.token);
  let bsInterval = $state<Interval>(data.interval as Interval);
  let bsBuckets = $state<VolumeBucket[]>(data.buckets);
  let bsSince = $state(data.since);
  let bsUntil = $state(data.until);
  let bsLoadedKey = $state(`${data.token}|${data.interval}|${data.under}|${data.over}`);
  let bsView = $state<View>(null);
  let bsHoverTime = $state<number | null>(null);
  let bsCollapsed = $state(false);
  let showBSPoint = $state(true);
  let showBSCumulative = $state(false);
  let bsMALength = $state(9);
  let bsMAType = $state<MAType>('sma');

  let bsXExtent = $derived<[number, number]>([unix(bsSince), unix(bsUntil)]);

  $effect(() => {
    const key = `${bsToken}|${bsInterval}|0|0`;
    if (key === bsLoadedKey) return;
    void loadBs();
  });

  async function loadBs() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(bsInterval);
      const qs = new URLSearchParams({
        token: bsToken,
        interval: bsInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        under: '10000',
        over: '100000',
        limit: '5000'
      });
      const res = await fetch(`/api/trade_volume?${qs}`);
      if (!res.ok) throw new Error(`trade_volume ${res.status}`);
      const body = await res.json();
      bsBuckets = body.buckets ?? [];
      bsSince = since.toISOString();
      bsUntil = until.toISOString();
      bsLoadedKey = `${bsToken}|${bsInterval}|0|0`;
      bsView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const BUYER_SELLER_SERIES = [
    { key: 'buyer_taker_usd', label: 'Buyer', color: '#22c55e' },
    { key: 'seller_taker_usd', label: 'Seller', color: '#ef4444' }
  ];
  const BUYER_SELLER_LINES = [
    {
      key: 'buyer_pct',
      label: '% Buyer',
      color: '#fbbf24',
      compute: (d: VolumeBucket) => {
        const total = d.buyer_taker_usd + d.seller_taker_usd;
        return total > 0 ? (d.buyer_taker_usd / total) * 100 : 0;
      }
    }
  ];

  let bsCumulativeLines = $derived.by(() => {
    if (bsBuckets.length === 0) return [];
    const buyerMA = maArray(
      bsBuckets.map((b) => b.buyer_taker_usd),
      bsMALength,
      bsMAType
    );
    const totalMA = maArray(
      bsBuckets.map((b) => b.buyer_taker_usd + b.seller_taker_usd),
      bsMALength,
      bsMAType
    );
    const tag = `${bsMAType.toUpperCase()}(${bsMALength})`;
    return [
      {
        key: 'cum_buyer',
        label: `% Buyer ${tag}`,
        color: '#fbbf24',
        dash: '5,3',
        compute: (_d: VolumeBucket, i: number) =>
          totalMA[i] > 0 ? (buyerMA[i] / totalMA[i]) * 100 : 0
      }
    ];
  });
  let bsLines = $derived(showBSCumulative ? [...BUYER_SELLER_LINES, ...bsCumulativeLines] : []);

  // -------- Volume by Trade Size ----------
  let szToken = $state(data.token);
  let szInterval = $state<Interval>(data.interval as Interval);
  let szBuckets = $state<VolumeBucket[]>(data.buckets);
  let szUnder = $state(data.under);
  let szOver = $state(data.over);
  let szUnderInput = $state(String(data.under));
  let szOverInput = $state(String(data.over));
  let szSince = $state(data.since);
  let szUntil = $state(data.until);
  let szLoadedKey = $state(`${data.token}|${data.interval}|${data.under}|${data.over}`);
  let szView = $state<View>(null);
  let szHoverTime = $state<number | null>(null);
  let szCollapsed = $state(false);
  let showSZPoint = $state(true);
  let showSZCumulative = $state(false);
  let szMALength = $state(9);
  let szMAType = $state<MAType>('sma');

  let szXExtent = $derived<[number, number]>([unix(szSince), unix(szUntil)]);

  $effect(() => {
    const key = `${szToken}|${szInterval}|${szUnder}|${szOver}`;
    if (key === szLoadedKey) return;
    void loadSz();
  });

  async function loadSz() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(szInterval);
      const qs = new URLSearchParams({
        token: szToken,
        interval: szInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        under: String(szUnder),
        over: String(szOver),
        limit: '5000'
      });
      const res = await fetch(`/api/trade_volume?${qs}`);
      if (!res.ok) throw new Error(`trade_volume ${res.status}`);
      const body = await res.json();
      szBuckets = body.buckets ?? [];
      szSince = since.toISOString();
      szUntil = until.toISOString();
      szLoadedKey = `${szToken}|${szInterval}|${szUnder}|${szOver}`;
      szView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function applySzThresholds() {
    const u = Number(szUnderInput);
    const o = Number(szOverInput);
    if (!Number.isFinite(u) || !Number.isFinite(o) || u < 0 || u >= o) {
      error = 'Require 0 ≤ under < over';
      return;
    }
    szUnder = u;
    szOver = o;
  }

  let sizeSeries = $derived([
    { key: 'small_usd', label: `< $${szUnder}`, color: '#3f3f46' },
    { key: 'mid_usd', label: `$${szUnder}–$${szOver}`, color: '#3b82f6' },
    { key: 'large_usd', label: `> $${szOver}`, color: '#a855f7' }
  ]);
  let sizeLines = $derived([
    {
      key: 'small_pct',
      label: `% < $${szUnder}`,
      color: '#fbbf24',
      compute: (d: VolumeBucket) => {
        const total = d.small_usd + d.mid_usd + d.large_usd;
        return total > 0 ? (d.small_usd / total) * 100 : 0;
      }
    },
    {
      key: 'large_pct',
      label: `% > $${szOver}`,
      color: '#06b6d4',
      compute: (d: VolumeBucket) => {
        const total = d.small_usd + d.mid_usd + d.large_usd;
        return total > 0 ? (d.large_usd / total) * 100 : 0;
      }
    }
  ]);

  let szCumulativeLines = $derived.by(() => {
    if (szBuckets.length === 0) return [];
    const smallMA = maArray(
      szBuckets.map((b) => b.small_usd),
      szMALength,
      szMAType
    );
    const largeMA = maArray(
      szBuckets.map((b) => b.large_usd),
      szMALength,
      szMAType
    );
    const totalMA = maArray(
      szBuckets.map((b) => b.small_usd + b.mid_usd + b.large_usd),
      szMALength,
      szMAType
    );
    const tag = `${szMAType.toUpperCase()}(${szMALength})`;
    return [
      {
        key: 'cum_small',
        label: `% < $${szUnder} ${tag}`,
        color: '#fbbf24',
        dash: '5,3',
        compute: (_d: VolumeBucket, i: number) =>
          totalMA[i] > 0 ? (smallMA[i] / totalMA[i]) * 100 : 0
      },
      {
        key: 'cum_large',
        label: `% > $${szOver} ${tag}`,
        color: '#06b6d4',
        dash: '5,3',
        compute: (_d: VolumeBucket, i: number) =>
          totalMA[i] > 0 ? (largeMA[i] / totalMA[i]) * 100 : 0
      }
    ];
  });
  let szLines = $derived(showSZCumulative ? [...sizeLines, ...szCumulativeLines] : []);

  // -------- Long/Short ratios (Top Traders + All) ----------
  // The two charts share the same LSR endpoint shape, but each has its own token+interval.
  let ttToken = $state(data.token);
  let ttInterval = $state<Interval>(data.interval as Interval);
  let ttData = $state<LongShortRow[]>(data.longShort);
  let ttSince = $state(data.since);
  let ttUntil = $state(data.until);
  let ttLoadedKey = $state(`${data.token}|${data.interval}`);
  let ttView = $state<View>(null);
  let ttHoverTime = $state<number | null>(null);
  let ttCollapsed = $state(false);
  let showTTPoint = $state(true);
  let showTTCumulative = $state(false);
  let ttMALength = $state(9);
  let ttMAType = $state<MAType>('sma');

  let ttXExtent = $derived<[number, number]>([unix(ttSince), unix(ttUntil)]);

  $effect(() => {
    const key = `${ttToken}|${ttInterval}`;
    if (key === ttLoadedKey) return;
    void loadTt();
  });

  async function loadTt() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(ttInterval);
      const qs = new URLSearchParams({
        token: ttToken,
        interval: ttInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const res = await fetch(`/api/long_short_ratios?${qs}`);
      if (!res.ok) throw new Error(`long_short_ratios ${res.status}`);
      const body = await res.json();
      ttData = body.series ?? [];
      ttSince = since.toISOString();
      ttUntil = until.toISOString();
      ttLoadedKey = `${ttToken}|${ttInterval}`;
      ttView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  let lsToken = $state(data.token);
  let lsInterval = $state<Interval>(data.interval as Interval);
  let lsData = $state<LongShortRow[]>(data.longShort);
  let lsSince = $state(data.since);
  let lsUntil = $state(data.until);
  let lsLoadedKey = $state(`${data.token}|${data.interval}`);
  let lsView = $state<View>(null);
  let lsHoverTime = $state<number | null>(null);
  let lsCollapsed = $state(false);
  let showLSPoint = $state(true);
  let showLSCumulative = $state(false);
  let lsMALength = $state(9);
  let lsMAType = $state<MAType>('sma');

  let lsXExtent = $derived<[number, number]>([unix(lsSince), unix(lsUntil)]);

  $effect(() => {
    const key = `${lsToken}|${lsInterval}`;
    if (key === lsLoadedKey) return;
    void loadLs();
  });

  async function loadLs() {
    loading = true;
    error = null;
    try {
      const { since, until } = lookbackWindow(lsInterval);
      const qs = new URLSearchParams({
        token: lsToken,
        interval: lsInterval,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const res = await fetch(`/api/long_short_ratios?${qs}`);
      if (!res.ok) throw new Error(`long_short_ratios ${res.status}`);
      const body = await res.json();
      lsData = body.series ?? [];
      lsSince = since.toISOString();
      lsUntil = until.toISOString();
      lsLoadedKey = `${lsToken}|${lsInterval}`;
      lsView = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const TOP_TRADERS_LINES = [
    {
      key: 'top_ct',
      label: 'Top traders (count)',
      color: '#fbbf24',
      compute: (d: LongShortRow) => d.top_trader_count_ratio
    },
    {
      key: 'top_vol',
      label: 'Top traders (vol)',
      color: '#06b6d4',
      compute: (d: LongShortRow) => d.top_trader_vol_ratio
    }
  ];
  const LS_LINES = [
    {
      key: 'all_ct',
      label: 'All (L/S count)',
      color: '#84cc16',
      compute: (d: LongShortRow) => d.long_short_count_ratio
    },
    {
      key: 'taker_vol',
      label: 'Taker L/S vol',
      color: '#a855f7',
      compute: (d: LongShortRow) => d.taker_long_short_vol_ratio
    }
  ];
  const NEUTRAL_REF = [{ value: 1 }];

  let ttCumulativeLines = $derived.by(() => {
    if (ttData.length === 0) return [];
    const countMA = maArray(
      ttData.map((d) => d.top_trader_count_ratio),
      ttMALength,
      ttMAType
    );
    const volMA = maArray(
      ttData.map((d) => d.top_trader_vol_ratio),
      ttMALength,
      ttMAType
    );
    const tag = `${ttMAType.toUpperCase()}(${ttMALength})`;
    return [
      {
        key: 'cum_top_ct',
        label: `Top count ${tag}`,
        color: '#fbbf24',
        dash: '5,3',
        compute: (_d: LongShortRow, i: number) => countMA[i]
      },
      {
        key: 'cum_top_vol',
        label: `Top vol ${tag}`,
        color: '#06b6d4',
        dash: '5,3',
        compute: (_d: LongShortRow, i: number) => volMA[i]
      }
    ];
  });
  let lsCumulativeLines = $derived.by(() => {
    if (lsData.length === 0) return [];
    const allCountMA = maArray(
      lsData.map((d) => d.long_short_count_ratio),
      lsMALength,
      lsMAType
    );
    const takerVolMA = maArray(
      lsData.map((d) => d.taker_long_short_vol_ratio),
      lsMALength,
      lsMAType
    );
    const tag = `${lsMAType.toUpperCase()}(${lsMALength})`;
    return [
      {
        key: 'cum_all_ct',
        label: `All L/S ct ${tag}`,
        color: '#84cc16',
        dash: '5,3',
        compute: (_d: LongShortRow, i: number) => allCountMA[i]
      },
      {
        key: 'cum_taker_vol',
        label: `Taker vol ${tag}`,
        color: '#a855f7',
        dash: '5,3',
        compute: (_d: LongShortRow, i: number) => takerVolMA[i]
      }
    ];
  });
  let ttLines = $derived([
    ...(showTTPoint ? TOP_TRADERS_LINES : []),
    ...(showTTCumulative ? ttCumulativeLines : [])
  ]);
  let lsLines = $derived([
    ...(showLSPoint ? LS_LINES : []),
    ...(showLSCumulative ? lsCumulativeLines : [])
  ]);

  // -------- Sync + view dispatch ----------
  function handleView(target: ChartId, v: View) {
    if (syncZoom) {
      sharedView = v;
      return;
    }
    if (target === 'ohlcv') ohlcvView = v;
    else if (target === 'bs') bsView = v;
    else if (target === 'sz') szView = v;
    else if (target === 'oi') oiView = v;
    else if (target === 'tt') ttView = v;
    else if (target === 'ls') lsView = v;
    else frView = v;
  }

  function handleHover(target: ChartId, t: number | null) {
    if (syncZoom) {
      sharedHoverTime = t;
      return;
    }
    if (target === 'ohlcv') ohlcvHoverTime = t;
    else if (target === 'bs') bsHoverTime = t;
    else if (target === 'sz') szHoverTime = t;
    else if (target === 'oi') oiHoverTime = t;
    else if (target === 'tt') ttHoverTime = t;
    else if (target === 'ls') lsHoverTime = t;
    else frHoverTime = t;
  }

  function toggleSync(next: boolean) {
    if (next) {
      sharedView = ohlcvView ?? bsView ?? szView ?? oiView ?? ttView ?? lsView ?? frView ?? null;
    } else {
      ohlcvView = sharedView;
      bsView = sharedView;
      szView = sharedView;
      oiView = sharedView;
      ttView = sharedView;
      lsView = sharedView;
      frView = sharedView;
    }
    syncZoom = next;
  }
</script>

<div class="p-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Trades</h1>
      <div class="text-xs text-zinc-500">Binance OHLCV + raw trades via DeFiStream</div>
    </div>
    <div class="flex items-end gap-3 flex-wrap">
      <label class="text-xs text-zinc-400 flex items-center gap-2">
        <input
          type="checkbox"
          checked={syncZoom}
          onchange={(e) => toggleSync(e.currentTarget.checked)}
          class="accent-zinc-400"
        />
        Sync zoom
      </label>
      {#if loading}
        <span class="text-xs text-zinc-500">loading…</span>
      {/if}
    </div>
  </div>

  {#if error}
    <div class="p-3 rounded border border-red-900 bg-red-950/40 text-sm text-red-300">{error}</div>
  {/if}

  <div class={pinOHLCV ? 'sticky top-0 z-20 shadow-xl shadow-black/60' : ''}>
    <ChartPanel title="OHLCV — {ohlcvToken} {ohlcvInterval}" bind:collapsed={ohlcvCollapsed}>
      {#snippet controls()}
        <select
          bind:value={ohlcvToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={ohlcvInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={pinOHLCV} class="accent-zinc-400" />
          Pin
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showOHLCVPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showOHLCVCumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={ohlcvMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={ohlcvMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if ohlcvCandles.length === 0}
        <div class="p-4 text-sm text-zinc-400">No OHLCV data.</div>
      {:else}
        <CandlestickChart
          candles={ohlcvCandles}
          lines={ohlcvLines}
          showCandles={showOHLCVPoint}
          xExtent={ohlcvXExtent}
          view={syncZoom ? sharedView : ohlcvView}
          onView={(v) => handleView('ohlcv', v)}
          hoverTime={syncZoom ? sharedHoverTime : ohlcvHoverTime}
          onHover={(t) => handleHover('ohlcv', t)}
        />
      {/if}
    </ChartPanel>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
    <ChartPanel title="Open Interest — {oiToken} {oiInterval}" bind:collapsed={oiCollapsed}>
      {#snippet controls()}
        <select
          bind:value={oiToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={oiInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showOIPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showOICumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={oiMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={oiMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if oiData.length === 0}
        <div class="p-4 text-sm text-zinc-400">No open-interest data.</div>
      {:else}
        <LineChart
          data={oiData}
          lines={oiLines}
          xExtent={oiXExtent}
          view={syncZoom ? sharedView : oiView}
          onView={(v) => handleView('oi', v)}
          hoverTime={syncZoom ? sharedHoverTime : oiHoverTime}
          onHover={(t) => handleHover('oi', t)}
          formatY={fmtUsdAxis}
          formatTooltip={fmtUsdTooltip}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Funding Rate — {frToken} {frInterval} (bps)" bind:collapsed={frCollapsed}>
      {#snippet controls()}
        <select
          bind:value={frToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={frInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showFRPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showFRCumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={frMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={frMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if frData.length === 0}
        <div class="p-4 text-sm text-zinc-400">No funding-rate data.</div>
      {:else}
        <SignedBarChart
          data={frDataBps}
          valueKey="rate_bps"
          lines={frLines}
          showBars={showFRPoint}
          valueLabel="Rate"
          xExtent={frXExtent}
          view={syncZoom ? sharedView : frView}
          onView={(v) => handleView('fr', v)}
          hoverTime={syncZoom ? sharedHoverTime : frHoverTime}
          onHover={(t) => handleHover('fr', t)}
          formatY={(v) => v.toFixed(2)}
          formatTooltip={(v) => `${v.toFixed(2)} bps`}
          minBarWidthPx={3}
        />
      {/if}
    </ChartPanel>

    <ChartPanel
      title="Buyer vs Seller Taker — {bsToken} {bsInterval}"
      bind:collapsed={bsCollapsed}
    >
      {#snippet controls()}
        <select
          bind:value={bsToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={bsInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showBSPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showBSCumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={bsMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={bsMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if bsBuckets.length === 0}
        <div class="p-4 text-sm text-zinc-400">No raw-trade data.</div>
      {:else}
        <StackedBarChart
          data={bsBuckets}
          series={showBSPoint ? BUYER_SELLER_SERIES : []}
          lines={bsLines}
          xExtent={bsXExtent}
          view={syncZoom ? sharedView : bsView}
          onView={(v) => handleView('bs', v)}
          hoverTime={syncZoom ? sharedHoverTime : bsHoverTime}
          onHover={(t) => handleHover('bs', t)}
        />
      {/if}
    </ChartPanel>

    <ChartPanel
      title="Volume by Trade Size — {szToken} {szInterval}"
      bind:collapsed={szCollapsed}
    >
      {#snippet controls()}
        <select
          bind:value={szToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={szInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <input
          bind:value={szUnderInput}
          type="number"
          step="100"
          min="0"
          title="Under threshold (USD)"
          class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <input
          bind:value={szOverInput}
          type="number"
          step="100"
          min="0"
          title="Over threshold (USD)"
          class="w-20 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <button
          onclick={applySzThresholds}
          class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          Apply
        </button>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showSZPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showSZCumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={szMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={szMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if szBuckets.length === 0}
        <div class="p-4 text-sm text-zinc-400">No raw-trade data.</div>
      {:else}
        <StackedBarChart
          data={szBuckets}
          series={showSZPoint ? sizeSeries : []}
          lines={szLines}
          xExtent={szXExtent}
          view={syncZoom ? sharedView : szView}
          onView={(v) => handleView('sz', v)}
          hoverTime={syncZoom ? sharedHoverTime : szHoverTime}
          onHover={(t) => handleHover('sz', t)}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Top Traders L/S — {ttToken} {ttInterval}" bind:collapsed={ttCollapsed}>
      {#snippet controls()}
        <select
          bind:value={ttToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={ttInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showTTPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showTTCumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={ttMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={ttMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if ttData.length === 0}
        <div class="p-4 text-sm text-zinc-400">No long/short data.</div>
      {:else}
        <LineChart
          data={ttData}
          lines={ttLines}
          refLines={NEUTRAL_REF}
          xExtent={ttXExtent}
          view={syncZoom ? sharedView : ttView}
          onView={(v) => handleView('tt', v)}
          hoverTime={syncZoom ? sharedHoverTime : ttHoverTime}
          onHover={(t) => handleHover('tt', t)}
          formatY={(v) => v.toFixed(2)}
          formatTooltip={(v) => v.toFixed(4)}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Long/Short — {lsToken} {lsInterval}" bind:collapsed={lsCollapsed}>
      {#snippet controls()}
        <select
          bind:value={lsToken}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
        <select
          bind:value={lsInterval}
          class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showLSPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showLSCumulative} class="accent-zinc-400" />
          MA
        </label>
        <input
          type="number"
          bind:value={lsMALength}
          min="2"
          max="500"
          step="1"
          class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
        />
        <select
          bind:value={lsMAType}
          class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      {/snippet}
      {#if lsData.length === 0}
        <div class="p-4 text-sm text-zinc-400">No long/short data.</div>
      {:else}
        <LineChart
          data={lsData}
          lines={lsLines}
          refLines={NEUTRAL_REF}
          xExtent={lsXExtent}
          view={syncZoom ? sharedView : lsView}
          onView={(v) => handleView('ls', v)}
          hoverTime={syncZoom ? sharedHoverTime : lsHoverTime}
          onHover={(t) => handleHover('ls', t)}
          formatY={(v) => v.toFixed(2)}
          formatTooltip={(v) => v.toFixed(4)}
        />
      {/if}
    </ChartPanel>
  </div>

  <div class="text-[11px] text-zinc-500">
    Each chart has its own Token + Interval. Sync zoom shares the time-range view across all
    visible charts (still works across charts with different intervals — the X axis is wall-clock
    time). Under/Over thresholds apply only to Volume by Trade Size.
  </div>
</div>
