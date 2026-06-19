<script lang="ts">
  // Experimental smart-wallet finder table. One bespoke tableview whose
  // "metric" selector swaps the right-most column AND the server-side ranking
  // (the top-N candidate set). Fixed core columns — Volume / OI / Realized /
  // Unrealized — plus the selected metric column. Header clicks re-sort the
  // *returned* set client-side (no refetch), same UX as WalletLeaderboardTable.
  //
  // Owns its own chrome: metric / lookback / token selectors + a 1-day-grain
  // snapshot slider. The min-days / min-volume noise guards live in the chart's
  // gear settings panel (owned by ChartInstance) since they're advanced knobs.

  import {
    SMART_WALLET_METRICS,
    SMART_WALLET_LOOKBACKS,
    smartWalletMetricDef,
    type SmartWalletMetric,
    type SmartWalletLookback
  } from '$lib/components/charts/config';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { onAuxClickWalletHl, onMouseDownSuppressMiddle } from '$lib/arkham';
  import { DAY_SLIDER_MAX_BACK, isoToBack, backToIso } from '$lib/daySlider';
  import { untrack } from 'svelte';

  export type SmartWalletRow = {
    wallet: string;
    volume: number;
    realized_pnl: number;
    unrealized_pnl: number;
    oi_token: number | null;
    oi_usd: number;
    metric: number;
    n_days: number;
    categories: string[];
  };

  let {
    rows = [],
    tokens = [],
    metric,
    lookback,
    token,
    snapshot,
    onChangeMetric,
    onChangeLookback,
    onChangeToken,
    onChangeSnapshot,
    loading = false,
    error = null
  }: {
    rows: SmartWalletRow[];
    tokens: string[];
    metric: SmartWalletMetric;
    lookback: SmartWalletLookback;
    token: string | null;
    snapshot: string; // resolved ISO date YYYY-MM-DD
    onChangeMetric: (m: SmartWalletMetric) => void;
    onChangeLookback: (l: SmartWalletLookback) => void;
    onChangeToken: (t: string | null) => void;
    onChangeSnapshot: (iso: string) => void;
    loading?: boolean;
    error?: string | null;
  } = $props();

  const metricDef = $derived(smartWalletMetricDef(metric));
  const isGlobal = $derived(token === null || token === '');

  function truncate(addr: string): string {
    if (!addr) return '';
    if (addr.length < 14) return addr;
    return addr.slice(0, 6) + '…' + addr.slice(-4);
  }
  function fmtUsd(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(0);
  }
  function fmtToken(n: number | null): string {
    if (n === null || !isFinite(n) || n === 0) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(2) + 'K';
    if (abs >= 1) return sign + abs.toFixed(2);
    return sign + abs.toPrecision(3);
  }
  function fmtMetric(n: number): string {
    if (!isFinite(n)) return '—';
    if (metricDef.format === 'usd') return fmtUsd(n);
    return (n >= 0 ? '+' : '') + n.toFixed(2);
  }

  let copiedAddr = $state('');
  async function copyAddr(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
      copiedAddr = addr;
      setTimeout(() => { if (copiedAddr === addr) copiedAddr = ''; }, 1200);
    } catch { /* no-op */ }
  }

  // ── Client-side resort. '' = server order (already sorted by the metric). ──
  let sortKey = $state<string>('');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = (sortDir === 1 ? -1 : 1);
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  let sortedRows = $derived.by(() => {
    if (!sortKey) return rows;
    const dir = sortDir;
    return [...rows].sort((a, b) => {
      const an = (a as unknown as Record<string, number>)[sortKey] ?? 0;
      const bn = (b as unknown as Record<string, number>)[sortKey] ?? 0;
      return (an - bn) * dir;
    });
  });

  // ── Snapshot day slider (oldest left → newest right). Local sliderPos is the
  // source of truth (bind:value) so dragging isn't fought by a re-asserted
  // one-way value; we push changes up via onChangeSnapshot and re-sync from the
  // `snapshot` prop only when it diverges externally (untrack guards the loop). ──
  const MAX_BACK = DAY_SLIDER_MAX_BACK;
  let sliderPos = $state(MAX_BACK); // synced from `snapshot` by the effect below
  $effect(() => {
    const want = MAX_BACK - isoToBack(snapshot);
    if (untrack(() => sliderPos) !== want) sliderPos = want;
  });
  function commitPos() {
    onChangeSnapshot(backToIso(MAX_BACK - sliderPos));
  }
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- Selectors row -->
  <div class="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-700 text-zinc-400">Smart Wallets</span>
    <span class="text-zinc-500">Metric:</span>
    <select
      value={metric}
      onchange={(e) => onChangeMetric(e.currentTarget.value as SmartWalletMetric)}
      title={metricDef.desc}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
    >
      {#each SMART_WALLET_METRICS as m (m.key)}
        <option value={m.key}>{m.label}</option>
      {/each}
    </select>

    <span class="text-zinc-500 ml-1">Lookback:</span>
    <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden">
      {#each SMART_WALLET_LOOKBACKS as l, i (l)}
        <button
          type="button"
          onclick={() => onChangeLookback(l)}
          class={'px-2 py-0.5 text-[11px] ' + (i > 0 ? 'border-l border-zinc-700 ' : '') + (lookback === l
            ? 'bg-zinc-800 text-zinc-100'
            : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title={`${l}-day window`}
        >{l}d</button>
      {/each}
    </div>

    <span class="text-zinc-500 ml-1">Token:</span>
    <select
      value={token ?? ''}
      onchange={(e) => onChangeToken(e.currentTarget.value === '' ? null : e.currentTarget.value)}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
    >
      <option value="">All tokens</option>
      {#each tokens as t (t)}
        <option value={t}>{t}</option>
      {/each}
    </select>

    <span class="text-[10px] text-zinc-600 ml-auto">
      {#if loading}loading…{:else}Click a column to re-sort{/if}
    </span>
  </div>

  <!-- Snapshot slider row (just below the header selectors) -->
  <div class="flex items-center gap-3 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900/30">
    <span class="text-zinc-500 whitespace-nowrap">Snapshot:</span>
    <span class="font-mono text-zinc-200 whitespace-nowrap">{snapshot}</span>
    <input
      type="range"
      min="0"
      max={MAX_BACK}
      step="1"
      bind:value={sliderPos}
      oninput={commitPos}
      class="flex-1 accent-blue-500 cursor-pointer"
      title="Drag to change the snapshot day (1-day grain)"
    />
    <button
      type="button"
      onclick={() => { sliderPos = MAX_BACK; commitPos(); }}
      class="text-[10px] text-zinc-500 hover:text-zinc-200 underline decoration-dotted whitespace-nowrap"
      title="Jump to the most recent day"
    >Today</button>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4">{error}</div>
    {:else if !loading && rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4">
        No wallets pass the filters for this window.<br />Lower the min-days / min-volume / min-realized / min-OI guards in settings.
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">#</th>
            <th class="text-left px-3 py-1.5 font-normal">Wallet</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('volume')} title="Window volume (USD)">Volume{sortArrow('volume')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort(isGlobal ? 'oi_usd' : 'oi_token')}
                title={isGlobal ? 'Open interest (USD) as of the snapshot' : 'Open interest (token units) as of the snapshot'}
            >OI{isGlobal ? ' ($)' : ` (${token})`}{sortArrow(isGlobal ? 'oi_usd' : 'oi_token')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('realized_pnl')} title="Realized PnL over the window (USD)">Realized{sortArrow('realized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('unrealized_pnl')} title="Unrealized PnL as of the snapshot (USD)">Unrealized{sortArrow('unrealized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                class:text-zinc-200={!sortKey || sortKey === 'metric'}
                onclick={() => onSort('metric')} title={metricDef.desc}>{metricDef.label}{sortArrow('metric')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as r, idx (r.wallet)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
              <td class="px-3 py-1">
                <button type="button" onclick={() => copyAddr(r.wallet)}
                        onauxclick={onAuxClickWalletHl(r.wallet)}
                        onmousedown={onMouseDownSuppressMiddle}
                        title={r.wallet + ' — click to copy · middle-click to open wallet page'}
                        class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer">
                  {copiedAddr === r.wallet ? '✓ copied' : truncate(r.wallet)}
                </button>
                {#if r.categories && r.categories.length > 0}
                  <span class="ml-1 inline-block text-[9px] uppercase tracking-wide px-1 py-0 rounded bg-zinc-900 border border-zinc-700 text-zinc-400"
                        title={r.categories.join(', ')}>{r.categories[0]}</span>
                {/if}
                <span class="ml-1 text-[9px] text-zinc-600">{r.n_days}d</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(r.volume)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">
                {#if isGlobal}{fmtUsd(r.oi_usd)}{:else}<div>{fmtToken(r.oi_token)}</div><div class="text-[10px] text-zinc-500">{fmtUsd(r.oi_usd)}</div>{/if}
              </td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={r.realized_pnl > 0}
                  class:text-rose-400={r.realized_pnl < 0}
                  class:text-zinc-500={r.realized_pnl === 0}>{fmtUsd(r.realized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={r.unrealized_pnl > 0}
                  class:text-rose-400={r.unrealized_pnl < 0}
                  class:text-zinc-500={r.unrealized_pnl === 0}>{fmtUsd(r.unrealized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono font-semibold"
                  class:text-emerald-400={r.metric > 0}
                  class:text-rose-400={r.metric < 0}
                  class:text-zinc-500={r.metric === 0}>{fmtMetric(r.metric)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
