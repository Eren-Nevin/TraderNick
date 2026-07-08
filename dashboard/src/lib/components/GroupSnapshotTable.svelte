<script lang="ts">
  // Group Snapshot: a wallet group's positions at the latest snapshot, combined into one
  // book per token (Σ size, size-weighted entry, Σ uPnL, wallet counts). Click a token →
  // the wallets holding it (reuses the backtracker positions dialog).
  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  type Row = {
    token: string; size_usd: number; entry: number; unrealized_pnl: number;
    wallets: number; n_long: number; n_short: number; long_usd: number; short_usd: number;
  };

  let {
    rows = [],
    loading = false,
    error = null,
    hasGroup = false,
    onTokenClick = (_t: string) => {}
  }: {
    rows?: Row[];
    loading?: boolean;
    error?: string | null;
    hasGroup?: boolean;
    onTokenClick?: (t: string) => void;
  } = $props();

  let sortKey = $state<string>('size_usd');
  let sortDir = $state<1 | -1>(-1);
  let search = $state('');
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = k === 'token' ? 1 : -1; }
  }
  const arrow = (k: string) => (sortKey !== k ? '' : sortDir === 1 ? ' ↑' : ' ↓');
  const netUsd = (r: Row) => (r.long_usd ?? 0) - (r.short_usd ?? 0);
  const sv = (r: Row, k: string): number | string =>
    k === 'token' ? r.token : k === 'net' ? netUsd(r) : ((r[k as keyof Row] as number) ?? 0);
  let sorted = $derived.by(() => {
    const arr = (rows as Row[]).filter((r) => r.token.toLowerCase().includes(search.trim().toLowerCase()));
    const k = sortKey, dir = sortDir;
    arr.sort((a, b) => {
      const av = sv(a, k), bv = sv(b, k);
      if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * dir;
      return (av - bv) * dir;
    });
    return arr;
  });

  function fmtUsd(n: number): string {
    const a = Math.abs(n), s = n < 0 ? '-' : '';
    if (a >= 1e9) return s + '$' + (a / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return s + '$' + (a / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return s + '$' + (a / 1e3).toFixed(1) + 'K';
    return s + '$' + a.toFixed(0);
  }
  const fmtPrice = (n: number) => (n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toPrecision(5));
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    <span class="text-[11px]">Group positions · latest snapshot</span>
    <input class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 w-24 focus:outline-none" placeholder="Token…" bind:value={search} />
    <span class="ml-auto text-[11px] text-zinc-500">{rows.length} tokens</span>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-60' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
    {:else if !hasGroup}
      <div class="h-full flex items-center justify-center text-zinc-500 px-4 text-center">Select a wallet group.</div>
    {:else if loading && rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
    {:else if rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No open positions in the group.</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('token')}>Token{arrow('token')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('size_usd')} title="Σ each wallet's position size ($)">Size{arrow('size_usd')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('net')} title="Net = long $ − short $">Net{arrow('net')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('entry')} title="Size-weighted average entry price">Entry{arrow('entry')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('unrealized_pnl')} title="Σ unrealized PnL">uPnL{arrow('unrealized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('wallets')} title="# wallets holding (long / short)">Wallets{arrow('wallets')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sorted as r (r.token)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/50 cursor-pointer" onclick={() => onTokenClick(r.token)} title="Show the wallets holding {r.token}">
              <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-200">{fmtUsd(r.size_usd)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {netUsd(r) > 0 ? 'text-emerald-400' : netUsd(r) < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtUsd(netUsd(r))}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400">{fmtPrice(r.entry)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {r.unrealized_pnl > 0 ? 'text-emerald-400' : r.unrealized_pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtUsd(r.unrealized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400 whitespace-nowrap">{r.wallets} <span class="text-emerald-500">{r.n_long}</span>/<span class="text-rose-500">{r.n_short}</span></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
