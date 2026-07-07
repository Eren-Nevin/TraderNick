<script lang="ts">
  // Latest individual trades (fills) for the HL wallet detail page — newest first,
  // with an optional token filter. Fed by /api/hyperliquid/wallet_fills.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { fmtTzDateTime } from '$lib/stores/timezone.svelte';

  export type TradeRow = {
    time: number; token: string; dir: string; side: string;
    price: number; size: number; value: number; closed_pnl: number; fee: number;
  };

  let {
    trades = [],
    loading = false,
    error = null
  }: {
    trades?: TradeRow[];
    loading?: boolean;
    error?: string | null;
  } = $props();

  let tokenFilter = $state('');
  const shown = $derived(
    tokenFilter.trim()
      ? trades.filter((t) => t.token.toLowerCase().includes(tokenFilter.trim().toLowerCase()))
      : trades
  );
  const isBuy = (t: TradeRow) => t.side === 'B';

  function fmtUsd(n: number): string {
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtNum(n: number): string {
    const a = Math.abs(n);
    if (a >= 1e3) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (a >= 1) return n.toFixed(3);
    return a === 0 ? '0' : n.toPrecision(3);
  }
  function fmtPrice(n: number): string {
    return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toPrecision(5);
  }
</script>

<div class="h-full flex flex-col text-sm border border-zinc-800 rounded-lg overflow-hidden" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-200 font-medium">Trades</span>
    {#if loading}
      <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading…"></span>
    {/if}
    <input
      class="ml-auto bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs text-zinc-200 w-24 focus:outline-none focus:border-zinc-500"
      placeholder="Token…" bind:value={tokenFilter} title="Filter by token" />
    <span class="text-zinc-500 text-xs whitespace-nowrap">{shown.length} fills</span>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-50' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4 py-8">{error}</div>
    {:else if shown.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">
        {trades.length === 0 ? 'No recent trades for this wallet.' : 'No trades match the filter.'}
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">Time</th>
            <th class="text-left px-3 py-1.5 font-normal">Token</th>
            <th class="text-left px-3 py-1.5 font-normal">Direction</th>
            <th class="text-right px-3 py-1.5 font-normal">Price</th>
            <th class="text-right px-3 py-1.5 font-normal">Size</th>
            <th class="text-right px-3 py-1.5 font-normal">Value</th>
            <th class="text-right px-3 py-1.5 font-normal">Realized PnL</th>
            <th class="text-right px-3 py-1.5 font-normal">Fee</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as t (t.time + t.token + t.dir + t.size + t.price)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-300 tabular-nums whitespace-nowrap">{fmtTzDateTime(t.time)}</td>
              <td class="px-3 py-1 text-zinc-200">{t.token}</td>
              <td class="px-3 py-1 whitespace-nowrap">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {isBuy(t)
                  ? 'border-emerald-800 text-emerald-400'
                  : 'border-rose-800 text-rose-400'}">{t.dir}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-300">{fmtPrice(t.price)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400">{fmtNum(t.size)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-300">{fmtUsd(t.value)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {t.closed_pnl > 0 ? 'text-emerald-400' : t.closed_pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">{t.closed_pnl ? fmtUsd(t.closed_pnl) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-500">{fmtUsd(t.fee)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
