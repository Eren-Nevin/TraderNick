<script lang="ts">
  // Positions table for the HL wallet detail page. Renders a normalized
  // PositionRow[] (see WalletPnl page for the live/history mappers). In `live`
  // mode it shows the rich columns the HL API provides (entry / liq / ROE /
  // funding / leverage); in history mode (stored snapshot) only the columns we
  // actually store (notional / size / unrealized). Client-sortable headers.

  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  export type PositionRow = {
    token: string;
    side: 'long' | 'short';
    amount: number;       // token units
    size_usd: number;     // notional USD
    unrealized_pnl: number;
    entry_px?: number | null;
    liquidation_px?: number | null;
    roe?: number | null;        // return on equity, as a fraction (0.12 = +12%)
    funding?: number | null;    // cumulative funding since open (USD)
    leverage?: number | null;
    leverage_type?: string | null;
  };

  let {
    positions = [],
    live = false,
    loading = false,
    error = null
  }: {
    positions: PositionRow[];
    live?: boolean;
    loading?: boolean;
    error?: string | null;
  } = $props();

  function fmtUsd(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtPrice(n: number | null | undefined): string {
    if (!n) return '—';
    if (n >= 1000) return n.toFixed(0);
    if (n >= 1) return n.toFixed(2);
    if (n >= 0.01) return n.toFixed(4);
    return n.toFixed(6);
  }
  function fmtSize(n: number): string {
    if (!n) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(2) + 'K';
    if (abs >= 1) return sign + abs.toFixed(2);
    return sign + abs.toFixed(4);
  }
  function fmtRoe(r: number | null | undefined): string {
    if (r === null || r === undefined || !isFinite(r)) return '—';
    return (r >= 0 ? '+' : '') + (r * 100).toFixed(1) + '%';
  }
  function fmtLev(v: number | null | undefined, t: string | null | undefined): string {
    if (!v) return '—';
    return v + '×' + (t ? ' ' + t[0].toUpperCase() : '');
  }

  // ── Client-side sort. '' = incoming order (server already sorts by notional). ──
  let sortKey = $state<string>('');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  let sortedRows = $derived.by(() => {
    if (!sortKey) return positions;
    const dir = sortDir;
    return [...positions].sort((a, b) => {
      const an = (a as unknown as Record<string, number>)[sortKey] ?? 0;
      const bn = (b as unknown as Record<string, number>)[sortKey] ?? 0;
      return (an - bn) * dir;
    });
  });

  const totalNotional = $derived(positions.reduce((s, p) => s + Math.abs(p.size_usd), 0));
  const totalUnreal = $derived(positions.reduce((s, p) => s + p.unrealized_pnl, 0));
</script>

<div class="h-full flex flex-col text-xs border border-zinc-800 rounded-lg overflow-hidden" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-200 font-medium">Positions</span>
    <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {live
      ? 'bg-emerald-950/40 border-emerald-800 text-emerald-400'
      : 'bg-zinc-900 border-zinc-700 text-zinc-400'}">{live ? 'Live' : 'Snapshot'}</span>
    {#if loading}
      <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading…"></span>
    {/if}
    {#if positions.length > 0}
      <span class="text-zinc-500 ml-auto">
        {positions.length} open · {fmtUsd(totalNotional)} notional ·
        <span class={totalUnreal > 0 ? 'text-emerald-400' : totalUnreal < 0 ? 'text-rose-400' : ''}>{fmtUsd(totalUnreal)}</span> uPnL
      </span>
    {/if}
  </div>
  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-50' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4 py-8">{error}</div>
    {:else if !loading && positions.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">
        No open positions{live ? '' : ' in our snapshot for this day'}.
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">Token</th>
            <th class="text-left px-3 py-1.5 font-normal">Side</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('size_usd')}>Notional{sortArrow('size_usd')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('amount')}>Size{sortArrow('amount')}</th>
            {#if live}
              <th class="text-right px-3 py-1.5 font-normal">Entry</th>
              <th class="text-right px-3 py-1.5 font-normal">Liq.</th>
            {/if}
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('unrealized_pnl')}>Unrealized{sortArrow('unrealized_pnl')}</th>
            {#if live}
              <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                  onclick={() => onSort('roe')}>ROE{sortArrow('roe')}</th>
              <th class="text-right px-3 py-1.5 font-normal">Funding</th>
              <th class="text-right px-3 py-1.5 font-normal">Lev.</th>
            {/if}
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as p (p.token + '|' + p.side)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-200">{p.token}</td>
              <td class="px-3 py-1">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {p.side === 'long'
                  ? 'bg-emerald-950/40 border-emerald-800 text-emerald-400'
                  : 'bg-rose-950/40 border-rose-800 text-rose-400'}">{p.side}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(p.size_usd)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtSize(p.amount)} {p.token}</td>
              {#if live}
                <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtPrice(p.entry_px)}</td>
                <td class="px-3 py-1 text-right font-mono text-amber-500/80">{fmtPrice(p.liquidation_px)}</td>
              {/if}
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={p.unrealized_pnl > 0}
                  class:text-rose-400={p.unrealized_pnl < 0}
                  class:text-zinc-500={p.unrealized_pnl === 0}>{fmtUsd(p.unrealized_pnl)}</td>
              {#if live}
                <td class="px-3 py-1 text-right font-mono"
                    class:text-emerald-400={(p.roe ?? 0) > 0}
                    class:text-rose-400={(p.roe ?? 0) < 0}
                    class:text-zinc-500={(p.roe ?? 0) === 0}>{fmtRoe(p.roe)}</td>
                <td class="px-3 py-1 text-right font-mono text-zinc-500">{p.funding != null ? fmtUsd(p.funding) : '—'}</td>
                <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtLev(p.leverage, p.leverage_type)}</td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
