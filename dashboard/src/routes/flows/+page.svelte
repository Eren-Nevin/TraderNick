<script lang="ts">
  import CandlestickChart from '$lib/components/CandlestickChart.svelte';
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import ChartPanel from '$lib/components/ChartPanel.svelte';
  import {
    INTERVALS,
    type Candle,
    type Interval,
    type TransferBucket,
    type TransferStream
  } from '$lib/api';
  import type { PageData } from './$types';
  import type { View } from '$lib/chart-zoom';
  import { defaultView } from '$lib/components/charts/config';

  let { data }: { data: PageData } = $props();

  type MAType = 'sma' | 'ema' | 'wma';
  type ChartId = 'ohlcv' | 'flow';

  let loading = $state(false);
  let error = $state<string | null>(null);

  let syncZoom = $state(true);
  let sharedView = $state<View>(null);
  let sharedHoverTime = $state<number | null>(null);

  const LOOKBACK_DAYS = 30;
  const OHLCV_LOOKBACK_DAYS: Record<Interval, number> = {
    '1m': 1,
    '5m': 3,
    '15m': 7,
    '30m': 14,
    '1h': 14,
    '4h': 30,
    '1d': 30
  };

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
  function maArray(vals: number[], n: number, t: MAType): number[] {
    if (t === 'ema') return emaArray(vals, n);
    if (t === 'wma') return wmaArray(vals, n);
    return smaArray(vals, n);
  }

  // -------- OHLCV ----------
  let ohlcvToken = $state(data.ohlcvToken);
  let ohlcvInterval = $state<Interval>(data.ohlcvInterval as Interval);
  let ohlcvCandles = $state<Candle[]>(data.candles);
  let ohlcvSince = $state(data.ohlcvSince);
  let ohlcvUntil = $state(data.ohlcvUntil);
  let ohlcvLoadedKey = $state(`${data.ohlcvToken}|${data.ohlcvInterval}`);
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
      const lookback = OHLCV_LOOKBACK_DAYS[ohlcvInterval];
      const now = new Date();
      const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
      const since = new Date(until.getTime() - lookback * 24 * 60 * 60 * 1000);
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
      ohlcvView = defaultView(ohlcvSince, ohlcvUntil);
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

  // -------- Transfer Volume (flow) ----------
  let chain = $state(data.chain);
  let token = $state(data.token);
  let interval = $state<Interval>(data.interval as Interval);
  let buckets = $state<TransferBucket[]>(data.buckets);
  let flowSince = $state(data.since);
  let flowUntil = $state(data.until);
  let flowLoadedKey = $state(`${data.chain}|${data.token}|${data.interval}`);
  let flowView = $state<View>(null);
  let flowHoverTime = $state<number | null>(null);
  let flowCollapsed = $state(false);
  let showPoint = $state(true);
  let showCumulative = $state(false);
  let maLength = $state(9);
  let maType = $state<MAType>('sma');

  let chains = $derived(
    Array.from(new Set(data.streams.map((s: TransferStream) => s.chain))).sort()
  );
  let tokensForChain = $derived(
    Array.from(new Set(data.streams.filter((s) => s.chain === chain).map((s) => s.token))).sort()
  );
  let kind = $derived(
    data.streams.find((s) => s.chain === chain && s.token === token)?.kind ?? 'erc20'
  );

  $effect(() => {
    if (tokensForChain.length > 0 && !tokensForChain.includes(token)) {
      token = tokensForChain[0];
    }
  });

  let flowXExtent = $derived<[number, number]>([unix(flowSince), unix(flowUntil)]);

  $effect(() => {
    const key = `${chain}|${token}|${interval}`;
    if (key === flowLoadedKey) return;
    void loadFlow();
  });

  async function loadFlow() {
    loading = true;
    error = null;
    try {
      const now = new Date();
      const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
      const since = new Date(until.getTime() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000);
      const k = data.streams.find((s) => s.chain === chain && s.token === token)?.kind ?? 'erc20';
      const qs = new URLSearchParams({
        chain,
        kind: k,
        token,
        interval,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '10000'
      });
      const res = await fetch(`/api/transfers/aggregate?${qs}`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      const body = await res.json();
      buckets = body.series ?? [];
      flowSince = since.toISOString();
      flowUntil = until.toISOString();
      flowLoadedKey = `${chain}|${token}|${interval}`;
      flowView = defaultView(flowSince, flowUntil);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const POINT_SERIES = [{ key: 'sum_amount', label: 'Amount', color: '#06b6d4' }];

  let cumulativeLines = $derived.by(() => {
    if (buckets.length === 0) return [];
    const ma = maArray(
      buckets.map((b) => b.sum_amount),
      maLength,
      maType
    );
    const tag = `${maType.toUpperCase()}(${maLength})`;
    return [
      {
        key: 'cum_amount',
        label: `Amount ${tag}`,
        color: '#fbbf24',
        dash: '5,3',
        scale: 'value' as const,
        compute: (_d: TransferBucket, i: number) => ma[i]
      }
    ];
  });
  let chartLines = $derived(showCumulative ? cumulativeLines : []);
  let chartSeries = $derived(showPoint ? POINT_SERIES : []);

  // -------- Sync + view dispatch ----------
  function handleView(target: ChartId, v: View) {
    if (syncZoom) {
      sharedView = v;
      return;
    }
    if (target === 'ohlcv') ohlcvView = v;
    else flowView = v;
  }

  function handleHover(target: ChartId, t: number | null) {
    if (syncZoom) {
      sharedHoverTime = t;
      return;
    }
    if (target === 'ohlcv') ohlcvHoverTime = t;
    else flowHoverTime = t;
  }

  function toggleSync(next: boolean) {
    if (next) {
      sharedView = ohlcvView ?? flowView ?? null;
    } else {
      ohlcvView = sharedView;
      flowView = sharedView;
    }
    syncZoom = next;
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Flows</h1>
      <div class="text-xs text-zinc-500">On-chain token transfers via DeFiStream</div>
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
        <div class="p-4 text-sm text-zinc-400">No OHLCV data for {ohlcvToken}.</div>
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

  <ChartPanel
    title="Transfer Volume — {token} on {chain}"
    bind:collapsed={flowCollapsed}
  >
    {#snippet controls()}
      <select
        bind:value={chain}
        class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
      >
        {#each chains as c (c)}
          <option value={c}>{c}</option>
        {/each}
      </select>
      <select
        bind:value={token}
        disabled={tokensForChain.length <= 1}
        class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {#each tokensForChain as t (t)}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <select
        bind:value={interval}
        class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
      >
        {#each INTERVALS as iv (iv)}
          <option value={iv}>{iv}</option>
        {/each}
      </select>
      <span class="text-[10px] uppercase tracking-widest text-zinc-500">{kind}</span>
      <label class="text-xs text-zinc-400 flex items-center gap-2">
        <input type="checkbox" bind:checked={showPoint} class="accent-zinc-400" />
        Point
      </label>
      <label class="text-xs text-zinc-400 flex items-center gap-2">
        <input type="checkbox" bind:checked={showCumulative} class="accent-zinc-400" />
        MA
      </label>
      <input
        type="number"
        bind:value={maLength}
        min="2"
        max="500"
        step="1"
        class="w-14 bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs"
      />
      <select
        bind:value={maType}
        class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-xs"
      >
        <option value="sma">SMA</option>
        <option value="ema">EMA</option>
        <option value="wma">WMA</option>
      </select>
    {/snippet}
    {#if buckets.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        No data for this stream — backfill via
        <code class="text-zinc-300">POST /jobs/backfill/&lt;kind&gt;_transfers</code>.
      </div>
    {:else}
      <StackedBarChart
        data={buckets}
        series={chartSeries}
        lines={chartLines}
        xExtent={flowXExtent}
        view={syncZoom ? sharedView : flowView}
        onView={(v) => handleView('flow', v)}
        hoverTime={syncZoom ? sharedHoverTime : flowHoverTime}
        onHover={(t) => handleHover('flow', t)}
      />
    {/if}
  </ChartPanel>

  <div class="text-[11px] text-zinc-500">
    OHLCV shows Binance price for the selected token. Transfer Volume aggregates DeFiStream
    on-chain transfers. Each chart has its own Token + Interval. Sync zoom shares the time-range
    view between them.
  </div>
</div>
