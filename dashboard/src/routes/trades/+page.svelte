<script lang="ts">
  import * as d3 from 'd3';
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import { INTERVALS, type Candle, type Interval, type VolumeBucket } from '$lib/api';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  let token = $state(data.token);
  let interval = $state<Interval>(data.interval as Interval);
  let underInput = $state(String(data.under));
  let overInput = $state(String(data.over));
  let under = $state(data.under);
  let over = $state(data.over);

  let candles = $state<Candle[]>(data.candles);
  let buckets = $state<VolumeBucket[]>(data.buckets);
  let loading = $state(false);
  let error = $state<string | null>(null);

  let syncZoom = $state(true);
  let sharedTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let ohlcvTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let bsTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);
  let szTransform = $state<d3.ZoomTransform>(d3.zoomIdentity);

  let sharedHoverTime = $state<number | null>(null);
  let ohlcvHoverTime = $state<number | null>(null);
  let bsHoverTime = $state<number | null>(null);
  let szHoverTime = $state<number | null>(null);

  let showBSPctLines = $state(true);
  let showSZPctLines = $state(true);
  let showBSBars = $state(true);
  let showSZBars = $state(true);

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

  let bsLines = $derived([
    ...(showBSPctLines ? BUYER_SELLER_LINES : []),
    ...bsCumulativeLines
  ]);

  let szLines = $derived([...(showSZPctLines ? sizeLines : []), ...szCumulativeLines]);

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
      const [ohlcvRes, tvRes] = await Promise.all([
        fetch(`/api/ohlcv?${ohlcvQS}`),
        fetch(`/api/trade_volume?${tvQS}`)
      ]);
      if (!ohlcvRes.ok) throw new Error(`ohlcv ${ohlcvRes.status}`);
      if (!tvRes.ok) throw new Error(`trade_volume ${tvRes.status}`);
      const ohlcvBody = await ohlcvRes.json();
      const tvBody = await tvRes.json();
      candles = ohlcvBody.candles ?? [];
      buckets = tvBody.buckets ?? [];
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

  function handleZoom(target: 'ohlcv' | 'bs' | 'sz', t: d3.ZoomTransform) {
    if (syncZoom) {
      sharedTransform = t;
    } else if (target === 'ohlcv') {
      ohlcvTransform = t;
    } else if (target === 'bs') {
      bsTransform = t;
    } else {
      szTransform = t;
    }
  }

  function handleHover(target: 'ohlcv' | 'bs' | 'sz', t: number | null) {
    if (syncZoom) {
      sharedHoverTime = t;
    } else if (target === 'ohlcv') {
      ohlcvHoverTime = t;
    } else if (target === 'bs') {
      bsHoverTime = t;
    } else {
      szHoverTime = t;
    }
  }

  function toggleSync(next: boolean) {
    if (next) {
      sharedTransform = ohlcvTransform;
    } else {
      ohlcvTransform = sharedTransform;
      bsTransform = sharedTransform;
      szTransform = sharedTransform;
    }
    syncZoom = next;
  }
</script>

<div class="p-4 space-y-4">
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
        Cumulative
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

  <div class="rounded border border-zinc-800 bg-zinc-950">
    {#if loading && candles.length === 0}
      <div class="p-4 text-sm text-zinc-400">Loading…</div>
    {:else if candles.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        No OHLCV data yet — wait for the live poller or fire a backfill.
      </div>
    {:else}
      <CandlestickChart
        {candles}
        {xExtent}
        transform={syncZoom ? sharedTransform : ohlcvTransform}
        onZoom={(t) => handleZoom('ohlcv', t)}
        hoverTime={syncZoom ? sharedHoverTime : ohlcvHoverTime}
        onHover={(t) => handleHover('ohlcv', t)}
      />
    {/if}
  </div>

  <div class="flex items-center justify-end gap-3 px-1">
    <label class="text-xs text-zinc-400 flex items-center gap-2">
      <input type="checkbox" bind:checked={showBSBars} class="accent-zinc-400" />
      Show Bars
    </label>
    <label class="text-xs text-zinc-400 flex items-center gap-2">
      <input type="checkbox" bind:checked={showBSPctLines} class="accent-zinc-400" />
      Show % lines
    </label>
  </div>

  <div class="rounded border border-zinc-800 bg-zinc-950">
    {#if buckets.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        No raw-trade data yet — start the raw_trades live poller or run the backfill.
      </div>
    {:else}
      <StackedBarChart
        data={buckets}
        series={showBSBars ? BUYER_SELLER_SERIES : []}
        lines={bsLines}
        title="Buyer vs Seller Taker Volume (USD)"
        {xExtent}
        transform={syncZoom ? sharedTransform : bsTransform}
        onZoom={(t) => handleZoom('bs', t)}
        hoverTime={syncZoom ? sharedHoverTime : bsHoverTime}
        onHover={(t) => handleHover('bs', t)}
      />
    {/if}
  </div>

  <div class="flex items-center justify-end gap-3 px-1">
    <label class="text-xs text-zinc-400 flex items-center gap-2">
      <input type="checkbox" bind:checked={showSZBars} class="accent-zinc-400" />
      Show Bars
    </label>
    <label class="text-xs text-zinc-400 flex items-center gap-2">
      <input type="checkbox" bind:checked={showSZPctLines} class="accent-zinc-400" />
      Show % lines
    </label>
  </div>

  <div class="rounded border border-zinc-800 bg-zinc-950">
    {#if buckets.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        No raw-trade data yet — start the raw_trades live poller or run the backfill.
      </div>
    {:else}
      <StackedBarChart
        data={buckets}
        series={showSZBars ? sizeSeries : []}
        lines={szLines}
        title="Volume by Trade Size (USD)"
        {xExtent}
        transform={syncZoom ? sharedTransform : szTransform}
        onZoom={(t) => handleZoom('sz', t)}
        hoverTime={syncZoom ? sharedHoverTime : szHoverTime}
        onHover={(t) => handleHover('sz', t)}
      />
    {/if}
  </div>

  <div class="text-[11px] text-zinc-500">
    Scroll to zoom, drag to pan, double-click to reset, hover for tooltips. Toggle Sync zoom to
    couple/uncouple the three charts' x-axis.
  </div>
</div>
