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
    onTokenClick,
    posMode = 'consensus',
  }: {
    rows: Record<string, unknown>[] | Row[];
    loading?: boolean;
    error?: string | null;
    hasGroup?: boolean;
    onTokenClick: (token: string) => void;
    posMode?: string;
  } = $props();

  // Positions column: group long/short at the snapshot (staleness-filtered). Count
  // (consensus) or OI value, per posMode. Green if longs dominate, red if shorts.
  function posLong(r: Row): number { return (posMode === 'oi' ? r.pos_oi_long : r.pos_n_long) ?? 0; }
  function posShort(r: Row): number { return (posMode === 'oi' ? r.pos_oi_short : r.pos_n_short) ?? 0; }
  function posMissing(r: Row): boolean { return r.pos_n_long == null && r.pos_n_short == null; }
  function posText(r: Row): string {
    if (posMissing(r)) return '—';
    return posMode === 'oi'
      ? `${fmtUsd(r.pos_oi_long ?? 0)}/${fmtUsd(r.pos_oi_short ?? 0)}`
      : `${r.pos_n_long ?? 0}/${r.pos_n_short ?? 0}`;
  }
  function posClass(r: Row): string {
    if (posMissing(r)) return 'text-zinc-600';
    const l = posLong(r), s = posShort(r);
    return l > s ? 'text-emerald-400' : l < s ? 'text-rose-400' : 'text-zinc-500';
  }
  // Positions ($): total long/short OI of the group's positions — always the $ value,
  // independent of the count/OI posMode toggle.
  function posOiText(r: Row): string {
    if (posMissing(r)) return '—';
    return `${fmtUsd(r.pos_oi_long ?? 0)}/${fmtUsd(r.pos_oi_short ?? 0)}`;
  }
  function posOiClass(r: Row): string {
    if (posMissing(r)) return 'text-zinc-600';
    const l = r.pos_oi_long ?? 0, s = r.pos_oi_short ?? 0;
    return l > s ? 'text-emerald-400' : l < s ? 'text-rose-400' : 'text-zinc-500';
  }
  // Positions Δ: change vs T−lookback (now − then), same count/OI toggle.
  function posDLong(r: Row): number { return (posMode === 'oi' ? r.pos_d_oi_long : r.pos_d_n_long) ?? 0; }
  function posDShort(r: Row): number { return (posMode === 'oi' ? r.pos_d_oi_short : r.pos_d_n_short) ?? 0; }
  function posDMissing(r: Row): boolean { return r.pos_d_n_long == null && r.pos_d_n_short == null; }
  const sgnUsd = (n: number) => (n > 0 ? '+' : '') + fmtUsd(n);
  const sgnInt = (n: number) => (n > 0 ? '+' : '') + Math.round(n);
  function posDText(r: Row): string {
    if (posDMissing(r)) return '—';
    return posMode === 'oi'
      ? `${sgnUsd(r.pos_d_oi_long ?? 0)}/${sgnUsd(r.pos_d_oi_short ?? 0)}`
      : `${sgnInt(r.pos_d_n_long ?? 0)}/${sgnInt(r.pos_d_n_short ?? 0)}`;
  }
  // Each Δ part is coloured by its OWN sign (positive = green, negative = red).
  const dSignClass = (n: number) => (n > 0 ? 'text-emerald-400' : n < 0 ? 'text-rose-400' : 'text-zinc-500');
  const dLongText = (r: Row) => (posMode === 'oi' ? sgnUsd(r.pos_d_oi_long ?? 0) : sgnInt(r.pos_d_n_long ?? 0));
  const dShortText = (r: Row) => (posMode === 'oi' ? sgnUsd(r.pos_d_oi_short ?? 0) : sgnInt(r.pos_d_n_short ?? 0));

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

  type Col = { key: string; label: string; kind: 'pct' | 'usd' | 'positions' | 'positions_oi' | 'positions_delta'; title?: string };
  const COLS: Col[] = [
    { key: 'price_pct', label: 'Price Δ%', kind: 'pct' },
    { key: 'price_vs_btc_pct', label: 'Δ vs BTC', kind: 'pct', title: 'Price change relative to BTC over the lookback (token/BTC ratio change). + = outperformed BTC; BTC = 0.' },
    { key: 'net_flow_group', label: 'Flow (grp)', kind: 'usd', title: "Selected group's net position flow ($) — buys − sells from fills" },
    { key: 'flow_group_pct', label: 'Flow (grp) Δ%', kind: 'pct', title: "Group flow / total OI at end of window (scale-free)" },
    { key: 'positions', label: 'Positions', kind: 'positions', title: "Group long/short positions at the snapshot (staleness-filtered) as Long/Short — count or OI value per the settings toggle" },
    { key: 'positions_oi', label: 'Positions ($)', kind: 'positions_oi', title: "Total long/short position size (OI, $) of the group's positions at the snapshot (staleness-filtered)" },
    { key: 'positions_delta', label: 'Positions Δ', kind: 'positions_delta', title: "Change in group Long/Short vs T−lookback (now − then). E.g. 20/12 now, 18/13 then → +2/−1. Same count/OI toggle." },
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
    const posNet = (r: Row) => posLong(r) - posShort(r); // sort Positions by net lean
    const posDNet = (r: Row) => posDLong(r) - posDShort(r); // sort Positions Δ by net change
    const sv = (r: Row): number | null => {
      if (k === 'positions') return posMissing(r) ? null : posNet(r);
      if (k === 'positions_oi') return posMissing(r) ? null : (r.pos_oi_long ?? 0) - (r.pos_oi_short ?? 0);
      if (k === 'positions_delta') return posDMissing(r) ? null : posDNet(r);
      return r[k] as number | null;
    };
    const s = [...arr].sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      const av = sv(a), bv = sv(b);
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
    if (c.kind === 'positions') return posText(r);
    if (c.kind === 'positions_oi') return posOiText(r);
    if (c.kind === 'positions_delta') return posDText(r);
    const v = r[c.key] as number | null;
    return c.kind === 'pct' ? fmtPct(v) : fmtUsd(v);
  }
  function signClass(r: Row, c: Col): string {
    if (c.kind === 'positions') return posClass(r);
    if (c.kind === 'positions_oi') return posOiClass(r);
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
                {#if c.kind === 'positions_delta'}
                  <td class="px-3 py-1 text-right font-mono whitespace-nowrap">
                    {#if posDMissing(r)}
                      <span class="text-zinc-600">—</span>
                    {:else}
                      <span class={dSignClass(posDLong(r))}>{dLongText(r)}</span><span class="text-zinc-600">/</span><span class={dSignClass(posDShort(r))}>{dShortText(r)}</span>
                    {/if}
                  </td>
                {:else}
                  <td class="px-3 py-1 text-right font-mono {signClass(r, c)}">{cellText(r, c)}</td>
                {/if}
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
