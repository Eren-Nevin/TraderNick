<script lang="ts">
  // Group Snapshot: a wallet group's positions at the latest snapshot, combined into one
  // book per token (Σ size, size-weighted entry, Σ uPnL, wallet counts). Click a token →
  // the wallets holding it (reuses the backtracker positions dialog).
  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  type Row = {
    token: string; size_usd: number; entry: number; unrealized_pnl: number;
    wallets: number; n_long: number; n_short: number; long_usd: number; short_usd: number;
    price_change_pct?: number | null; price_change_pct_btc?: number | null;
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

  let sortKey = $state<string>('netsize');
  let sortDir = $state<1 | -1>(-1);
  let search = $state('');
  let sideFilter = $state<'' | 'long' | 'short'>('');
  let minSize = $state(0);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = k === 'token' ? 1 : -1; }
  }
  const arrow = (k: string) => (sortKey !== k ? '' : sortDir === 1 ? ' ↑' : ' ↓');
  const netUsd = (r: Row) => (r.long_usd ?? 0) - (r.short_usd ?? 0);
  const netSize = (r: Row) => Math.abs(netUsd(r));
  const sideLabel = (r: Row) => (netUsd(r) > 0 ? 'Long' : netUsd(r) < 0 ? 'Short' : 'Flat');
  // Net long = # long wallets − # short wallets; parenthesis = long:short ratio %.
  const netLong = (r: Row) => (r.n_long ?? 0) - (r.n_short ?? 0);
  // long / (long + short) as a fraction (0..1); null when there are no wallets.
  const lsFrac = (r: Row) => {
    const t = (r.n_long ?? 0) + (r.n_short ?? 0);
    return t ? (r.n_long ?? 0) / t : null;
  };
  const lsRatio = (r: Row) => {
    const f = lsFrac(r);
    return f == null ? 'N/A' : Math.round(100 * f) + '%';
  };
  const sv = (r: Row, k: string): number | string =>
    k === 'token' ? r.token
      : k === 'net' || k === 'side' ? netUsd(r)
      : k === 'netsize' ? netSize(r)
      : k === 'netlong' ? (lsFrac(r) ?? -1) // sort by the long-share %
      : ((r[k as keyof Row] as number) ?? 0);
  // Distinct tokens in the group (plus the current pick, so it survives a reload where
  // that token drops out), sorted for the selector.
  const tokenOpts = $derived(
    [...new Set([...(search ? [search] : []), ...(rows as Row[]).map((r) => r.token)])].sort()
  );
  let sorted = $derived.by(() => {
    const minUsd = (minSize || 0) * 1000; // input is in $K
    const arr = (rows as Row[]).filter(
      (r) =>
        (!search || r.token === search) &&
        (!sideFilter || (sideFilter === 'long' ? netUsd(r) > 0 : netUsd(r) < 0)) &&
        netSize(r) >= minUsd
    );
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
  const fmtPct = (n: number | null | undefined) => (n == null ? '—' : (n > 0 ? '+' : '') + n.toFixed(2) + '%');
  const pctCls = (n: number | null | undefined) =>
    n == null ? 'text-zinc-600' : n > 0 ? 'text-emerald-400' : n < 0 ? 'text-rose-400' : 'text-zinc-500';
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    <span class="text-[11px]">Group positions · latest snapshot</span>
    <select class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 focus:outline-none" bind:value={search} title="Filter by token">
      <option value="">All tokens</option>
      {#each tokenOpts as tok (tok)}<option value={tok}>{tok}</option>{/each}
    </select>
    <select class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 focus:outline-none" bind:value={sideFilter} title="Filter by net side">
      <option value="">All sides</option>
      <option value="long">Long</option>
      <option value="short">Short</option>
    </select>
    <input type="number" min="0" step="100" class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 w-20 focus:outline-none" placeholder="Min $K" title="Minimum total size, in $K (e.g. 1000 = $1M)" bind:value={minSize} />
    <span class="ml-auto text-[11px] text-zinc-500">{sorted.length}{sorted.length !== rows.length ? ` / ${rows.length}` : ''} tokens</span>
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
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('side')} title="Net side (long $ vs short $)">Side{arrow('side')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('netsize')} title="|long $ − short $| — net directional exposure">Net Size{arrow('netsize')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('entry')} title="Size-weighted average entry price">Entry{arrow('entry')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('price_change_pct')} title="Price change % over the Δprice lookback">Chg %{arrow('price_change_pct')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('price_change_pct_btc')} title="Price change % relative to BTC (priced in BTC) over the Δprice lookback">Chg %/BTC{arrow('price_change_pct_btc')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('unrealized_pnl')} title="Σ unrealized PnL">uPnL{arrow('unrealized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('long_usd')} title="Σ long positions ($); count in ()">Longs{arrow('long_usd')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('short_usd')} title="Σ short positions ($); count in ()">Shorts{arrow('short_usd')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('netlong')} title="# long − # short wallets; () = long / (long + short) as %. Sorts by the %.">Net Long{arrow('netlong')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sorted as r (r.token)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/50 cursor-pointer" onclick={() => onTokenClick(r.token)} title="Show the wallets holding {r.token}">
              <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
              <td class="px-3 py-1">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {netUsd(r) > 0
                  ? 'border-emerald-800 text-emerald-400' : netUsd(r) < 0 ? 'border-rose-800 text-rose-400' : 'border-zinc-700 text-zinc-500'}">{sideLabel(r)}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-200">{fmtUsd(netSize(r))}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400">{fmtPrice(r.entry)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {pctCls(r.price_change_pct)}">{fmtPct(r.price_change_pct)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {pctCls(r.price_change_pct_btc)}">{fmtPct(r.price_change_pct_btc)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {r.unrealized_pnl > 0 ? 'text-emerald-400' : r.unrealized_pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">{fmtUsd(r.unrealized_pnl)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-emerald-400 whitespace-nowrap">{r.n_long ? `${fmtUsd(r.long_usd)} (${r.n_long})` : '—'}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-rose-400 whitespace-nowrap">{r.n_short ? `${fmtUsd(r.short_usd)} (${r.n_short})` : '—'}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums whitespace-nowrap {netLong(r) > 0 ? 'text-emerald-400' : netLong(r) < 0 ? 'text-rose-400' : 'text-zinc-500'}">{netLong(r) > 0 ? '+' : ''}{netLong(r)} <span class="text-zinc-500">({lsRatio(r)})</span></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
