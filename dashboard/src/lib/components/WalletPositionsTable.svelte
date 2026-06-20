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
    opened_at?: number | null;  // unix seconds the position was opened (snapshot only)
  };

  let {
    positions = [],
    live = false,
    loading = false,
    error = null,
    // Token whose close-price is overlaid on the PnL chart (max one). The row
    // toggle is single-select: picking a token replaces any prior one.
    selectedToken = null,
    onToggleToken = undefined
  }: {
    positions: PositionRow[];
    live?: boolean;
    loading?: boolean;
    error?: string | null;
    selectedToken?: string | null;
    onToggleToken?: (token: string) => void;
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

<div class="h-full flex flex-col text-sm border border-zinc-800 rounded-lg overflow-hidden" use:stopDragEvents>
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
            <th class="text-center px-2 py-1.5 font-normal" title="Overlay this token's close price on the PnL chart">Price</th>
            <th class="text-left px-3 py-1.5 font-normal">Token</th>
            <th class="text-left px-3 py-1.5 font-normal">Side</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('size_usd')}>Notional{sortArrow('size_usd')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('amount')}>Size{sortArrow('amount')}</th>
            <th class="text-right px-3 py-1.5 font-normal">Entry</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('unrealized_pnl')}>Unrealized{sortArrow('unrealized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
                onclick={() => onSort('roe')}>ROE{sortArrow('roe')}</th>
            <th class="text-right px-3 py-1.5 font-normal">Funding</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as p (p.token + '|' + p.side)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40"
                class:bg-blue-950={selectedToken === p.token}>
              <td class="px-2 py-1 text-center">
                <button type="button" onclick={() => onToggleToken?.(p.token)}
                  title={selectedToken === p.token ? 'Hide close-price overlay' : 'Overlay this token’s close price on the PnL chart'}
                  aria-pressed={selectedToken === p.token}
                  class="inline-flex h-4 w-4 items-center justify-center rounded-full border transition-colors {selectedToken === p.token
                    ? 'bg-blue-500 border-blue-400'
                    : 'border-zinc-600 hover:border-blue-400'}">
                  {#if selectedToken === p.token}<span class="h-2 w-2 rounded-full bg-white"></span>{/if}
                </button>
              </td>
              <td class="px-3 py-1 font-mono text-zinc-200">{p.token}</td>
              <td class="px-3 py-1">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {p.side === 'long'
                  ? 'bg-emerald-950/40 border-emerald-800 text-emerald-400'
                  : 'bg-rose-950/40 border-rose-800 text-rose-400'}">{p.side}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(p.size_usd)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtSize(p.amount)} {p.token}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtPrice(p.entry_px)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={p.unrealized_pnl > 0}
                  class:text-rose-400={p.unrealized_pnl < 0}
                  class:text-zinc-500={p.unrealized_pnl === 0}>{fmtUsd(p.unrealized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={(p.roe ?? 0) > 0}
                  class:text-rose-400={(p.roe ?? 0) < 0}
                  class:text-zinc-500={(p.roe ?? 0) === 0}>{fmtRoe(p.roe)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-500">{p.funding != null ? fmtUsd(p.funding) : '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
