<script lang="ts">
  // Early Movers tableview: wallets ranked by how well they predicted the detected
  // price moves. Long / Short columns are correct / incorrect / missed. Served by
  // /api/hyperliquid/early_movers; sort + limit + fuzzy-filter are client-side.
  // Middle-click an address → the HL wallet page (token pre-selected).
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';

  type Row = {
    wallet: string;
    correct_long: number; incorrect_long: number; missed_long: number;
    correct_short: number; incorrect_short: number; missed_short: number;
    avg_size?: number;
    categories?: string[];
  };

  let {
    rows = [],
    loading = false,
    error = null,
    snapshotDate = '',
    mode = 'flow',
    totalLong = 0,
    totalShort = 0,
    totalBars = 0
  }: {
    rows: Row[];
    loading?: boolean;
    error?: string | null;
    snapshotDate?: string;
    mode?: 'flow' | 'open_flip' | 'position_state';
    totalLong?: number;
    totalShort?: number;
    totalBars?: number;
  } = $props();

  const pct = (n: number) => (totalBars > 0 ? ((n / totalBars) * 100).toFixed(1) : '0.0');
  const avgLabel = $derived(mode === 'position_state' ? 'Avg Position' : 'Avg Flow');
  function fmtUsd(n: number | null | undefined): string {
    if (n == null || !isFinite(n)) return '—';
    const a = Math.abs(n);
    if (a >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
    return `$${n.toFixed(0)}`;
  }

  let sortKey = $state<string>('correct_total');
  let sortDir = $state<1 | -1>(-1);
  let limit = $state<'25' | '50' | 'all'>('50');
  let search = $state('');
  // Min-value filters (client-side, on the returned top-N).
  let minLong = $state(0);
  let minShort = $state(0);
  let minSize = $state(0);

  function fuzzy(q: string, t: string): boolean {
    q = q.trim().toLowerCase();
    if (!q) return true;
    t = t.toLowerCase();
    let i = 0;
    for (const ch of t) { if (ch === q[i]) i++; if (i === q.length) return true; }
    return false;
  }
  const sortVal = (r: Row, k: string): number => {
    if (k === 'correct_total') return r.correct_long + r.correct_short;
    if (k === 'long') return r.correct_long;
    if (k === 'short') return r.correct_short;
    if (k === 'incorrect_total') return r.incorrect_long + r.incorrect_short;
    if (k === 'avg_size') return r.avg_size ?? 0;
    return 0;
  };
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  let sortedRows = $derived.by(() => {
    const arr = rows.filter(
      (r) =>
        fuzzy(search, r.wallet) &&
        r.correct_long >= (minLong || 0) &&
        r.correct_short >= (minShort || 0) &&
        (r.avg_size ?? 0) >= (minSize || 0)
    );
    const dir = sortDir, k = sortKey;
    const s = [...arr].sort((a, b) => (sortVal(a, k) - sortVal(b, k)) * dir);
    return limit === 'all' ? s : s.slice(0, Number(limit));
  });

  const selClass =
    'bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 hover:border-zinc-600 focus:outline-none';
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    <span class="text-[11px]">
      <span class="text-emerald-400">{totalLong} long ({pct(totalLong)}%)</span>
      / <span class="text-rose-400">{totalShort} short ({pct(totalShort)}%)</span>
      of {totalBars} bars · correct/incorrect/missed
    </span>
    <input class={selClass + ' w-24'} placeholder="Wallet…" bind:value={search} title="Fuzzy filter by wallet" />
    <label class="flex items-center gap-1 text-[10px] text-zinc-500" title="Min correct long">≥L
      <input type="number" min="0" class={selClass + ' w-11'} bind:value={minLong} /></label>
    <label class="flex items-center gap-1 text-[10px] text-zinc-500" title="Min correct short">≥S
      <input type="number" min="0" class={selClass + ' w-11'} bind:value={minShort} /></label>
    <label class="flex items-center gap-1 text-[10px] text-zinc-500" title="Min {avgLabel.toLowerCase()} size ($)">≥$
      <input type="number" min="0" step="1000" class={selClass + ' w-16'} bind:value={minSize} /></label>
    <span class="ml-auto text-[11px]">Show</span>
    <select class={selClass} bind:value={limit} title="Number of rows to show">
      <option value="25">25</option>
      <option value="50">50</option>
      <option value="all">All</option>
    </select>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
    {:else if loading && rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
    {:else if rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No wallets / no moves in range</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">#</th>
            <th class="text-left px-3 py-1.5 font-normal">Wallet</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              title="Long moves: correct (opened long) / incorrect (opened short) / missed" onclick={() => onSort('long')}>Long{sortArrow('long')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              title="Short moves: correct (opened short) / incorrect (opened long) / missed" onclick={() => onSort('short')}>Short{sortArrow('short')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              title="Total correct (long + short)" onclick={() => onSort('correct_total')}>Correct{sortArrow('correct_total')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              title="Average size ($) of the wallet's identifying position/flow across the moves it reacted to" onclick={() => onSort('avg_size')}>{avgLabel}{sortArrow('avg_size')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as r, idx (r.wallet)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
              <td class="px-3 py-1">
                <WalletAddress address={r.wallet} auxKind="wallet" snapshot={snapshotDate} tags={r.categories ?? []} />
              </td>
              <td class="px-3 py-1 text-right font-mono whitespace-nowrap">
                <span class="text-emerald-400">{r.correct_long}</span><span class="text-zinc-600">/</span><span class="text-rose-400">{r.incorrect_long}</span><span class="text-zinc-600">/</span><span class="text-zinc-500">{r.missed_long}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono whitespace-nowrap">
                <span class="text-emerald-400">{r.correct_short}</span><span class="text-zinc-600">/</span><span class="text-rose-400">{r.incorrect_short}</span><span class="text-zinc-600">/</span><span class="text-zinc-500">{r.missed_short}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-200">{r.correct_long + r.correct_short}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(r.avg_size)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
