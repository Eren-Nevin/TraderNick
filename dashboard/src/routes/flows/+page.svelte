<script lang="ts">
  import StackedBarChart from '$lib/components/StackedBarChart.svelte';
  import ChartPanel from '$lib/components/ChartPanel.svelte';
  import {
    INTERVALS,
    type Interval,
    type TransferBucket,
    type TransferStream
  } from '$lib/api';
  import type { PageData } from './$types';
  import type { View } from '$lib/chart-zoom';

  let { data }: { data: PageData } = $props();

  let chain = $state(data.chain);
  let token = $state(data.token);
  let interval = $state<Interval>(data.interval as Interval);
  let buckets = $state<TransferBucket[]>(data.buckets);
  let loading = $state(false);
  let error = $state<string | null>(null);

  const LOOKBACK_DAYS = 30;

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

  let sinceUnix = $derived(Math.floor(new Date(data.since).getTime() / 1000));
  let untilUnix = $derived(Math.floor(new Date(data.until).getTime() / 1000));
  let xExtent = $derived<[number, number]>([sinceUnix, untilUnix]);

  let view = $state<View>(null);
  let hoverTime = $state<number | null>(null);
  let chartCollapsed = $state(false);

  // Per-chart Point / MA toggles — same shape as Trades charts.
  let showPoint = $state(true);
  let showCumulative = $state(false);
  let maLength = $state(9);
  let maType = $state<'sma' | 'ema' | 'wma'>('sma');

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
  function maArray(vals: number[], n: number, t: MAType): number[] {
    if (t === 'ema') return emaArray(vals, n);
    if (t === 'wma') return wmaArray(vals, n);
    return smaArray(vals, n);
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

  $effect(() => {
    if (chain === data.chain && token === data.token && interval === data.interval) return;
    void reload(chain, token, interval);
  });

  async function reload(c: string, t: string, iv: Interval) {
    loading = true;
    error = null;
    try {
      const now = new Date();
      const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
      const since = new Date(until.getTime() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000);
      const k = data.streams.find((s) => s.chain === c && s.token === t)?.kind ?? 'erc20';
      const qs = new URLSearchParams({
        chain: c,
        kind: k,
        token: t,
        interval: iv,
        since: since.toISOString(),
        until: until.toISOString(),
        limit: '10000'
      });
      const res = await fetch(`/api/transfers/aggregate?${qs}`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      const body = await res.json();
      buckets = body.series ?? [];
      data.since = since.toISOString();
      data.until = until.toISOString();
      data.chain = c;
      data.token = t;
      data.interval = iv;
      view = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }
</script>

<div class="p-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Flows</h1>
      <div class="text-xs text-zinc-500">On-chain token transfers via DeFiStream</div>
    </div>
    <div class="flex items-end gap-3 flex-wrap">
      <label class="text-xs text-zinc-400">
        Chain
        <select
          bind:value={chain}
          class="ml-2 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
        >
          {#each chains as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
      </label>
      <label class="text-xs text-zinc-400">
        Token
        <select
          bind:value={token}
          disabled={tokensForChain.length <= 1}
          class="ml-2 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {#each tokensForChain as t (t)}
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
      <div class="text-[10px] uppercase tracking-widest text-zinc-500 ml-2">kind: {kind}</div>
    </div>
  </div>

  {#if error}
    <div class="p-3 rounded border border-red-900 bg-red-950/40 text-sm text-red-300">{error}</div>
  {/if}

  <ChartPanel title="Transfer Volume — {token} on {chain}" bind:collapsed={chartCollapsed}>
    {#snippet controls()}
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
    {#if loading && buckets.length === 0}
      <div class="p-4 text-sm text-zinc-400">Loading…</div>
    {:else if buckets.length === 0}
      <div class="p-4 text-sm text-zinc-400">
        No data for this stream yet — backfill it via
        <code class="text-zinc-300">POST /jobs/backfill/&lt;kind&gt;_transfers</code>.
      </div>
    {:else}
      <StackedBarChart
        data={buckets}
        series={chartSeries}
        lines={chartLines}
        {xExtent}
        {view}
        onView={(v) => (view = v)}
        {hoverTime}
        onHover={(t) => (hoverTime = t)}
      />
    {/if}
  </ChartPanel>

  <div class="text-[11px] text-zinc-500">
    Scroll to zoom, drag to pan, double-click to reset, hover for tooltip. MA overlay smooths the
    bar series with SMA / EMA / WMA of the chosen length.
  </div>
</div>
