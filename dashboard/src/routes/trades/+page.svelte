<script lang="ts">
  import * as d3 from 'd3';
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

  let token = $state(data.token);
  let interval = $state<Interval>(data.interval as Interval);
  let underInput = $state(String(data.under));
  let overInput = $state(String(data.over));
  let under = $state(data.under);
  let over = $state(data.over);

  let candles = $state<Candle[]>(data.candles);
  let buckets = $state<VolumeBucket[]>(data.buckets);
  let openInterest = $state<OpenInterestRow[]>(data.openInterest);
  let longShort = $state<LongShortRow[]>(data.longShort);
  let fundingRate = $state<FundingRateRow[]>(data.fundingRate);
  let loading = $state(false);
  let error = $state<string | null>(null);

  let syncZoom = $state(true);
  let sharedTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let ohlcvTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let bsTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let szTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let oiTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let ttTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let lsTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let frTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);

  let sharedHoverTime = $state<number | null>(null);
  let ohlcvHoverTime = $state<number | null>(null);
  let bsHoverTime = $state<number | null>(null);
  let szHoverTime = $state<number | null>(null);
  let oiHoverTime = $state<number | null>(null);
  let ttHoverTime = $state<number | null>(null);
  let lsHoverTime = $state<number | null>(null);
  let frHoverTime = $state<number | null>(null);

  type ChartId = 'ohlcv' | 'bs' | 'sz' | 'oi' | 'tt' | 'ls' | 'fr';

  let bsCollapsed = $state(false);
  let szCollapsed = $state(false);
  let ttCollapsed = $state(false);
  let lsCollapsed = $state(false);
  let oiCollapsed = $state(false);
  let frCollapsed = $state(false);

  let showOHLCVPoint = $state(true);
  let showOHLCVCumulative = $state(true);
  let showBSPoint = $state(true);
  let showBSCumulative = $state(true);
  let showSZPoint = $state(true);
  let showSZCumulative = $state(true);
  let showOIPoint = $state(true);
  let showOICumulative = $state(true);
  let showTTPoint = $state(true);
  let showTTCumulative = $state(true);
  let showLSPoint = $state(true);
  let showLSCumulative = $state(true);
  let showFRPoint = $state(true);
  let showFRCumulative = $state(true);

  let cumulativeEnabled = $state(false);
  let cumulativeLengthInput = $state('9');
  let cumulativeTypeInput = $state<'sma' | 'ema' | 'wma'>('sma');
  let cumulativeLength = $state(9);
  let cumulativeType = $state<'sma' | 'ema' | 'wma'>('sma');

  type MAType = 'sma' | 'ema' | 'wma';

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

  let sinceUnix = $derived(Math.floor(new Date(data.since).getTime() / 1000));
  let untilUnix = $derived(Math.floor(new Date(data.until).getTime() / 1000));
  let xExtent = $derived<[number, number]>([sinceUnix, untilUnix]);

  const LOOKBACK_DAYS: Record<Interval, number> = {
    '1m': 1,
    '5m': 3,
    '15m': 7,
    '30m': 14,
    '1h': 14,
    '4h': 30,
    '1d': 30
  };

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

  let sizeSeries = $derived([
    { key: 'small_usd', label: `< $${under}`, color: '#3f3f46' },
    { key: 'mid_usd', label: `$${under}–$${over}`, color: '#3b82f6' },
    { key: 'large_usd', label: `> $${over}`, color: '#a855f7' }
  ]);

  let sizeLines = $derived([
    {
      key: 'small_pct',
      label: `% < $${under}`,
      color: '#fbbf24',
      compute: (d: VolumeBucket) => {
        const total = d.small_usd + d.mid_usd + d.large_usd;
        return total > 0 ? (d.small_usd / total) * 100 : 0;
      }
    },
    {
      key: 'large_pct',
      label: `% > $${over}`,
      color: '#06b6d4',
      compute: (d: VolumeBucket) => {
        const total = d.small_usd + d.mid_usd + d.large_usd;
        return total > 0 ? (d.large_usd / total) * 100 : 0;
      }
    }
  ]);

  let bsCumulativeLines = $derived.by(() => {
    if (!cumulativeEnabled || buckets.length === 0) return [];
    const buyerMA = maArray(
      buckets.map((b) => b.buyer_taker_usd),
      cumulativeLength,
      cumulativeType
    );
    const totalMA = maArray(
      buckets.map((b) => b.buyer_taker_usd + b.seller_taker_usd),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
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

  let szCumulativeLines = $derived.by(() => {
    if (!cumulativeEnabled || buckets.length === 0) return [];
    const smallMA = maArray(
      buckets.map((b) => b.small_usd),
      cumulativeLength,
      cumulativeType
    );
    const largeMA = maArray(
      buckets.map((b) => b.large_usd),
      cumulativeLength,
      cumulativeType
    );
    const totalMA = maArray(
      buckets.map((b) => b.small_usd + b.mid_usd + b.large_usd),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
    return [
      {
        key: 'cum_small',
        label: `% < $${under} ${tag}`,
        color: '#fbbf24',
        dash: '5,3',
        compute: (_d: VolumeBucket, i: number) =>
          totalMA[i] > 0 ? (smallMA[i] / totalMA[i]) * 100 : 0
      },
      {
        key: 'cum_large',
        label: `% > $${over} ${tag}`,
        color: '#06b6d4',
        dash: '5,3',
        compute: (_d: VolumeBucket, i: number) =>
          totalMA[i] > 0 ? (largeMA[i] / totalMA[i]) * 100 : 0
      }
    ];
  });

  let bsLines = $derived(
    showBSCumulative ? [...BUYER_SELLER_LINES, ...bsCumulativeLines] : []
  );

  let szLines = $derived(
    showSZCumulative ? [...sizeLines, ...szCumulativeLines] : []
  );

  const OI_LINES = [
    {
      key: 'oi_usd',
      label: 'OI (USD)',
      color: '#06b6d4',
      compute: (d: OpenInterestRow) => d.open_interest_value
    }
  ];

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

  let fundingRateBps = $derived(
    fundingRate.map((d) => ({ ...d, rate_bps: d.rate * 10000 }))
  );

  let oiCumulativeLines = $derived.by(() => {
    if (!cumulativeEnabled || openInterest.length === 0) return [];
    const ma = maArray(
      openInterest.map((d) => d.open_interest_value),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
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

  let frCumulativeLines = $derived.by(() => {
    if (!cumulativeEnabled || fundingRateBps.length === 0) return [];
    const ma = maArray(
      fundingRateBps.map((d) => d.rate_bps),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
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

  let oiLines = $derived([
    ...(showOIPoint ? OI_LINES : []),
    ...(showOICumulative ? oiCumulativeLines : [])
  ]);

  let frLines = $derived(showFRCumulative ? frCumulativeLines : []);

  let ohlcvCumulativeLines = $derived.by(() => {
    if (!cumulativeEnabled || candles.length === 0) return [];
    const ma = maArray(
      candles.map((c) => c.close),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
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

  let ttCumulativeLines = $derived.by(() => {
    if (!cumulativeEnabled || longShort.length === 0) return [];
    const countMA = maArray(
      longShort.map((d) => d.top_trader_count_ratio),
      cumulativeLength,
      cumulativeType
    );
    const volMA = maArray(
      longShort.map((d) => d.top_trader_vol_ratio),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
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
    if (!cumulativeEnabled || longShort.length === 0) return [];
    const allCountMA = maArray(
      longShort.map((d) => d.long_short_count_ratio),
      cumulativeLength,
      cumulativeType
    );
    const takerVolMA = maArray(
      longShort.map((d) => d.taker_long_short_vol_ratio),
      cumulativeLength,
      cumulativeType
    );
    const tag = `${cumulativeType.toUpperCase()}(${cumulativeLength})`;
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

  function applyCumulativeSettings() {
    const n = Math.max(2, Math.min(500, Math.round(Number(cumulativeLengthInput) || 9)));
    cumulativeLength = n;
    cumulativeLengthInput = String(n);
    cumulativeType = cumulativeTypeInput;
  }

  $effect(() => {
    if (
      token === data.token &&
      interval === data.interval &&
      under === data.under &&
      over === data.over
    )
      return;
    void reload(token, interval, under, over);
  });

  function resetAllTransforms() {
    sharedTransform = d3.zoomIdentity;
    ohlcvTransform = d3.zoomIdentity;
    bsTransform = d3.zoomIdentity;
    szTransform = d3.zoomIdentity;
    oiTransform = d3.zoomIdentity;
    ttTransform = d3.zoomIdentity;
    lsTransform = d3.zoomIdentity;
    frTransform = d3.zoomIdentity;
  }

  async function reload(t: string, iv: Interval, u: number, o: number) {
    loading = true;
    error = null;
    try {
      const now = new Date();
      const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
      const lookback = LOOKBACK_DAYS[iv];
      const since = new Date(until.getTime() - lookback * 24 * 60 * 60 * 1000);
      const ohlcvQS = new URLSearchParams({
        token: t,
        interval: iv,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const tvQS = new URLSearchParams({
        ...Object.fromEntries(ohlcvQS),
        under: String(u),
        over: String(o)
      });
      const derivQS = new URLSearchParams({
        token: t,
        interval: iv,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '5000'
      });
      const [ohlcvRes, tvRes, oiRes, lsRes, frRes] = await Promise.all([
        fetch(`/api/ohlcv?${ohlcvQS}`),
        fetch(`/api/trade_volume?${tvQS}`),
        fetch(`/api/open_interest?${derivQS}`),
        fetch(`/api/long_short_ratios?${derivQS}`),
        fetch(`/api/funding_rate?${derivQS}`)
      ]);
      if (!ohlcvRes.ok) throw new Error(`ohlcv ${ohlcvRes.status}`);
      if (!tvRes.ok) throw new Error(`trade_volume ${tvRes.status}`);
      if (!oiRes.ok) throw new Error(`open_interest ${oiRes.status}`);
      if (!lsRes.ok) throw new Error(`long_short_ratios ${lsRes.status}`);
      if (!frRes.ok) throw new Error(`funding_rate ${frRes.status}`);
      const ohlcvBody = await ohlcvRes.json();
      const tvBody = await tvRes.json();
      const oiBody = await oiRes.json();
      const lsBody = await lsRes.json();
      const frBody = await frRes.json();
      candles = ohlcvBody.candles ?? [];
      buckets = tvBody.buckets ?? [];
      openInterest = oiBody.series ?? [];
      longShort = lsBody.series ?? [];
      fundingRate = frBody.series ?? [];
      data.since = since.toISOString();
      data.until = until.toISOString();
      resetAllTransforms();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function applyThresholds() {
    const u = Number(underInput);
    const o = Number(overInput);
    if (!Number.isFinite(u) || !Number.isFinite(o) || u < 0 || u >= o) {
      error = 'Require 0 ≤ under < over';
      return;
    }
    under = u;
    over = o;
  }

  function handleZoom(target: ChartId, t: d3.ZoomTransform) {
    if (syncZoom) {
      sharedTransform = t;
      return;
    }
    if (target === 'ohlcv') ohlcvTransform = t;
    else if (target === 'bs') bsTransform = t;
    else if (target === 'sz') szTransform = t;
    else if (target === 'oi') oiTransform = t;
    else if (target === 'tt') ttTransform = t;
    else if (target === 'ls') lsTransform = t;
    else frTransform = t;
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
      sharedTransform = ohlcvTransform;
    } else {
      ohlcvTransform = sharedTransform;
      bsTransform = sharedTransform;
      szTransform = sharedTransform;
      oiTransform = sharedTransform;
      ttTransform = sharedTransform;
      lsTransform = sharedTransform;
      frTransform = sharedTransform;
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
      <label class="text-xs text-zinc-400">
        Token
        <select
          bind:value={token}
          class="ml-2 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        >
          {#each data.tokens as t (t)}
            <option value={t}>{t}</option>
          {/each}
        </select>
      </label>
      <label class="text-xs text-zinc-400">
        Interval
        <select
          bind:value={interval}
          class="ml-2 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        >
          {#each INTERVALS as iv (iv)}
            <option value={iv}>{iv}</option>
          {/each}
        </select>
      </label>
      <label class="text-xs text-zinc-400">
        Under
        <input
          bind:value={underInput}
          type="number"
          step="100"
          min="0"
          class="ml-2 w-24 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        />
      </label>
      <label class="text-xs text-zinc-400">
        Over
        <input
          bind:value={overInput}
          type="number"
          step="100"
          min="0"
          class="ml-2 w-24 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        />
      </label>
      <button
        onclick={applyThresholds}
        class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-3 py-1 text-sm"
      >
        Apply
      </button>
      <label class="text-xs text-zinc-400 flex items-center gap-2 ml-2">
        <input
          type="checkbox"
          checked={syncZoom}
          onchange={(e) => toggleSync(e.currentTarget.checked)}
          class="accent-zinc-400"
        />
        Sync zoom
      </label>
      <label class="text-xs text-zinc-400 flex items-center gap-2 ml-2">
        <input
          type="checkbox"
          bind:checked={cumulativeEnabled}
          class="accent-zinc-400"
        />
        MA
      </label>
      <label class="text-xs text-zinc-400">
        N
        <input
          bind:value={cumulativeLengthInput}
          type="number"
          min="2"
          max="500"
          step="1"
          class="ml-2 w-16 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        />
      </label>
      <label class="text-xs text-zinc-400">
        Type
        <select
          bind:value={cumulativeTypeInput}
          class="ml-2 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        >
          <option value="sma">SMA</option>
          <option value="ema">EMA</option>
          <option value="wma">WMA</option>
        </select>
      </label>
      <button
        onclick={applyCumulativeSettings}
        class="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-3 py-1 text-sm"
      >
        Apply MA
      </button>
    </div>
  </div>

  {#if error}
    <div class="p-3 rounded border border-red-900 bg-red-950/40 text-sm text-red-300">{error}</div>
  {/if}

  <ChartPanel title="OHLCV">
    {#snippet controls()}
      <label class="text-xs text-zinc-400 flex items-center gap-2">
        <input type="checkbox" bind:checked={showOHLCVPoint} class="accent-zinc-400" />
        Point
      </label>
      <label class="text-xs text-zinc-400 flex items-center gap-2">
        <input type="checkbox" bind:checked={showOHLCVCumulative} class="accent-zinc-400" />
        MA
      </label>
    {/snippet}
    {#if loading && candles.length === 0}
      <div class="p-4 text-sm text-zinc-400">Loading…</div>
    {:else if candles.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        No OHLCV data yet — wait for the live poller or fire a backfill.
      </div>
    {:else}
      <CandlestickChart
        {candles}
        lines={ohlcvLines}
        showCandles={showOHLCVPoint}
        {xExtent}
        transform={syncZoom ? sharedTransform : ohlcvTransform}
        onZoom={(t) => handleZoom('ohlcv', t)}
        hoverTime={syncZoom ? sharedHoverTime : ohlcvHoverTime}
        onHover={(t) => handleHover('ohlcv', t)}
      />
    {/if}
  </ChartPanel>

  <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
    <ChartPanel title="Open Interest (USD)" bind:collapsed={oiCollapsed}>
      {#snippet controls()}
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showOIPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showOICumulative} class="accent-zinc-400" />
          MA
        </label>
      {/snippet}
      {#if openInterest.length === 0}
        <div class="p-4 text-sm text-zinc-400">
          No open-interest data yet — start the binance_open_interest live poller or run the backfill.
        </div>
      {:else}
        <LineChart
          data={openInterest}
          lines={oiLines}
          {xExtent}
          transform={syncZoom ? sharedTransform : oiTransform}
          onZoom={(t) => handleZoom('oi', t)}
          hoverTime={syncZoom ? sharedHoverTime : oiHoverTime}
          onHover={(t) => handleHover('oi', t)}
          formatY={fmtUsdAxis}
          formatTooltip={fmtUsdTooltip}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Funding Rate (bps)" bind:collapsed={frCollapsed}>
      {#snippet controls()}
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showFRPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showFRCumulative} class="accent-zinc-400" />
          MA
        </label>
      {/snippet}
      {#if fundingRate.length === 0}
        <div class="p-4 text-sm text-zinc-400">
          No funding-rate data yet — start the binance_funding_rate live poller or run the backfill.
        </div>
      {:else}
        <SignedBarChart
          data={fundingRateBps}
          valueKey="rate_bps"
          lines={frLines}
          showBars={showFRPoint}
          valueLabel="Rate"
          {xExtent}
          transform={syncZoom ? sharedTransform : frTransform}
          onZoom={(t) => handleZoom('fr', t)}
          hoverTime={syncZoom ? sharedHoverTime : frHoverTime}
          onHover={(t) => handleHover('fr', t)}
          formatY={(v) => v.toFixed(2)}
          formatTooltip={(v) => `${v.toFixed(2)} bps`}
          minBarWidthPx={3}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Buyer vs Seller Taker Volume (USD)" bind:collapsed={bsCollapsed}>
      {#snippet controls()}
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showBSPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showBSCumulative} class="accent-zinc-400" />
          MA
        </label>
      {/snippet}
      {#if buckets.length === 0}
        <div class="p-4 text-sm text-zinc-400">
          No raw-trade data yet — start the raw_trades live poller or run the backfill.
        </div>
      {:else}
        <StackedBarChart
          data={buckets}
          series={showBSPoint ? BUYER_SELLER_SERIES : []}
          lines={bsLines}
          {xExtent}
          transform={syncZoom ? sharedTransform : bsTransform}
          onZoom={(t) => handleZoom('bs', t)}
          hoverTime={syncZoom ? sharedHoverTime : bsHoverTime}
          onHover={(t) => handleHover('bs', t)}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Volume by Trade Size (USD)" bind:collapsed={szCollapsed}>
      {#snippet controls()}
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showSZPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showSZCumulative} class="accent-zinc-400" />
          MA
        </label>
      {/snippet}
      {#if buckets.length === 0}
        <div class="p-4 text-sm text-zinc-400">
          No raw-trade data yet — start the raw_trades live poller or run the backfill.
        </div>
      {:else}
        <StackedBarChart
          data={buckets}
          series={showSZPoint ? sizeSeries : []}
          lines={szLines}
          {xExtent}
          transform={syncZoom ? sharedTransform : szTransform}
          onZoom={(t) => handleZoom('sz', t)}
          hoverTime={syncZoom ? sharedHoverTime : szHoverTime}
          onHover={(t) => handleHover('sz', t)}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Top Traders L/S Ratios" bind:collapsed={ttCollapsed}>
      {#snippet controls()}
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showTTPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showTTCumulative} class="accent-zinc-400" />
          MA
        </label>
      {/snippet}
      {#if longShort.length === 0}
        <div class="p-4 text-sm text-zinc-400">
          No long/short data yet — start the binance_long_short_ratios live poller or run the backfill.
        </div>
      {:else}
        <LineChart
          data={longShort}
          lines={ttLines}
          refLines={NEUTRAL_REF}
          {xExtent}
          transform={syncZoom ? sharedTransform : ttTransform}
          onZoom={(t) => handleZoom('tt', t)}
          hoverTime={syncZoom ? sharedHoverTime : ttHoverTime}
          onHover={(t) => handleHover('tt', t)}
          formatY={(v) => v.toFixed(2)}
          formatTooltip={(v) => v.toFixed(4)}
        />
      {/if}
    </ChartPanel>

    <ChartPanel title="Long/Short Ratios" bind:collapsed={lsCollapsed}>
      {#snippet controls()}
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showLSPoint} class="accent-zinc-400" />
          Point
        </label>
        <label class="text-xs text-zinc-400 flex items-center gap-2">
          <input type="checkbox" bind:checked={showLSCumulative} class="accent-zinc-400" />
          MA
        </label>
      {/snippet}
      {#if longShort.length === 0}
        <div class="p-4 text-sm text-zinc-400">
          No long/short data yet — start the binance_long_short_ratios live poller or run the backfill.
        </div>
      {:else}
        <LineChart
          data={longShort}
          lines={lsLines}
          refLines={NEUTRAL_REF}
          {xExtent}
          transform={syncZoom ? sharedTransform : lsTransform}
          onZoom={(t) => handleZoom('ls', t)}
          hoverTime={syncZoom ? sharedHoverTime : lsHoverTime}
          onHover={(t) => handleHover('ls', t)}
          formatY={(v) => v.toFixed(2)}
          formatTooltip={(v) => v.toFixed(4)}
        />
      {/if}
    </ChartPanel>

  </div>

  <div class="text-[11px] text-zinc-500">
    Scroll to zoom, drag to pan, double-click to reset, hover for tooltips. Toggle Sync zoom to
    couple/uncouple all visible charts' x-axis. Collapse a chart via its ▼/▶ button — when sync is
    on, expanding it picks up the current shared zoom and hover automatically.
  </div>
</div>
