<script lang="ts">
  // Backtracker Leaderboard tableview (the `backtracker_leaderboard` chart kind).
  // One row per HL-perp token, served by /api/hyperliquid/backtracker_leaderboard:
  // price Δ%, net flow (group + overall taker CVD $), net-signed-OI Δ%, long/short
  // Δ%, volume Δ%, and spot volume-delta ($ + %) over the toolbar lookback. The
  // server returns the full set; sort + the 10/30/All limit + token filter are
  // client-side. Click a row → the Net Position dialog for that token.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  type Row = Record<string, number | null> & { token: string };

  let {
    rows = [],
    loading = false,
    error = null,
    hasGroup = false,
    onTokenClick
  }: {
    rows: Record<string, unknown>[] | Row[];
    loading?: boolean;
    error?: string | null;
    hasGroup?: boolean;
    onTokenClick: (token: string) => void;
  } = $props();

  function fmtUsd(n: number | null): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtPct(n: number | null): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  type Col = { key: string; label: string; kind: 'pct' | 'usd'; title?: string };
  const COLS: Col[] = [
    { key: 'price_pct', label: 'Price Δ%', kind: 'pct' },
    { key: 'price_vs_btc_pct', label: 'Δ vs BTC', kind: 'pct', title: 'Price change relative to BTC over the lookback (token/BTC ratio change). + = outperformed BTC; BTC = 0.' },
    { key: 'net_flow_group', label: 'Flow (grp)', kind: 'usd', title: "Selected group's net position flow ($) — buys − sells from fills" },
    { key: 'flow_group_pct', label: 'Flow (grp) Δ%', kind: 'pct', title: "Group flow / total OI at end of window (scale-free)" },
    { key: 'net_flow_overall', label: 'Flow (all)', kind: 'usd', title: 'Market-wide net taker flow (CVD $) over the lookback' },
    { key: 'flow_overall_pct', label: 'Flow Δ%', kind: 'pct', title: 'Overall flow / total OI at end of window (scale-free)' },
    { key: 'net_oi_now_pct', label: 'Net OI %', kind: 'pct', title: 'Current net signed OI / total OI at end (directional lean)' },
    { key: 'net_oi_pct', label: 'Net OI Δ%', kind: 'pct', title: '(net_signed_OI end − start) / total OI end (the CHANGE)' },
    { key: 'long_pct', label: 'Long Δ%', kind: 'pct', title: 'Long OI vs its value at the start of the lookback' },
    { key: 'short_pct', label: 'Short Δ%', kind: 'pct', title: 'Short OI vs its value at the start of the lookback' },
    { key: 'vol_pct', label: 'Vol Δ%', kind: 'pct', title: 'Volume this window vs the immediately preceding equal window' },
    { key: 'spot_vd', label: 'Spot VD', kind: 'usd', title: 'Binance-spot volume delta ($) over the lookback' },
    { key: 'spot_vd_pct', label: 'Spot VD %', kind: 'pct', title: 'Spot volume delta / total spot volume' }
  ];

  let sortKey = $state<string>('net_flow_overall');
  let sortDir = $state<1 | -1>(-1);
  let limit = $state<'10' | '30' | 'all'>('30');
  let search = $state('');

  function fuzzy(q: string, t: string): boolean {
    q = q.trim().toLowerCase();
    if (!q) return true;
    t = t.toLowerCase();
    let i = 0;
    for (const ch of t) { if (ch === q[i]) i++; if (i === q.length) return true; }
    return false;
  }
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = k === 'token' ? 1 : -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  let sortedRows = $derived.by(() => {
    const arr = (rows as Row[]).filter((r) => fuzzy(search, String(r.token)));
    const dir = sortDir, k = sortKey;
    const s = [...arr].sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      const av = a[k] as number | null, bv = b[k] as number | null;
      const an = av === null || av === undefined || !isFinite(av as number);
      const bn = bv === null || bv === undefined || !isFinite(bv as number);
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      // normal signed sort (negatives first when ascending)
      return ((av as number) - (bv as number)) * dir;
    });
    return limit === 'all' ? s : s.slice(0, Number(limit));
  });

  function cellText(r: Row, c: Col): string {
    const v = r[c.key] as number | null;
    return c.kind === 'pct' ? fmtPct(v) : fmtUsd(v);
  }
  function signClass(r: Row, c: Col): string {
    const v = r[c.key] as number | null;
    if (v === null || v === undefined || !isFinite(v) || v === 0) return 'text-zinc-500';
    return v > 0 ? 'text-emerald-400' : 'text-rose-400';
  }

  const selClass =
    'bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 hover:border-zinc-600 focus:outline-none';
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    <span class="text-[11px]">Click a token → position book · header to sort</span>
    <button class={selClass} onclick={() => (sortDir = sortDir === 1 ? -1 : 1)}
      title="Toggle ascending / descending">{sortDir === 1 ? 'Asc ↑' : 'Desc ↓'}</button>
    <input class={selClass + ' w-24'} placeholder="Token…" bind:value={search} title="Fuzzy filter by token" />
    <span class="ml-auto text-[11px]">Show</span>
    <select class={selClass} bind:value={limit} title="Number of rows to show">
      <option value="10">10</option>
      <option value="30">30</option>
      <option value="all">All</option>
    </select>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
    {:else if loading && (rows as Row[]).length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
    {:else if (rows as Row[]).length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No tokens</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">#</th>
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              onclick={() => onSort('token')}>Token{sortArrow('token')}</th>
            {#each COLS as c (c.key)}
              <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none {c.key === 'net_flow_group' && !hasGroup ? 'opacity-40' : ''}"
                title={c.title} onclick={() => onSort(c.key)}>{c.label}{sortArrow(c.key)}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each sortedRows as r, idx (r.token)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/50 cursor-pointer"
              onclick={() => onTokenClick(String(r.token))}
              title="Open the position book for {r.token}">
              <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
              <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
              {#each COLS as c (c.key)}
                <td class="px-3 py-1 text-right font-mono {signClass(r, c)}">{cellText(r, c)}</td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
