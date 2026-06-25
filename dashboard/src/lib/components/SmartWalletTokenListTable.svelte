<script lang="ts">
  // Smart Wallets (Dynamic) "Token List" view. One row per token the filtered
  // wallet cohort holds: long/short OI summed across those wallets at the latest
  // position snapshot, the absolute change vs 24h-ago and 7d-ago, and HL-perp
  // 24h/7d price change. Served by /api/hyperliquid/smart_wallet_token_list.
  //
  // The server returns every held token; sorting is entirely client-side here —
  // click a column header to re-cut. `unit` ($/token, from the toolbar) flips the
  // OI + OI-change columns; price Δ columns are always %. Default: Long OI desc.

  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  type Row = {
    token: string;
    long_oi_token: number; long_oi_usd: number;
    short_oi_token: number; short_oi_usd: number;
    long_chg24_token: number; long_chg24_usd: number;
    short_chg24_token: number; short_chg24_usd: number;
    long_chg7d_token: number; long_chg7d_usd: number;
    short_chg7d_token: number; short_chg7d_usd: number;
    pct_24h: number | null; pct_7d: number | null;
  };

  let {
    rows = [],
    loading = false,
    error = null,
    unit = 'usd'
  }: {
    rows: Record<string, unknown>[] | Row[];
    loading?: boolean;
    error?: string | null;
    unit?: 'usd' | 'token';
  } = $props();

  function fmtUsd(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtAmount(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(1) + 'K';
    return sign + abs.toFixed(2);
  }
  function fmtPct(n: number | null): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const sign = n > 0 ? '+' : '';
    return sign + n.toFixed(2) + '%';
  }

  type SortKey =
    | 'token'
    | 'long_oi_token' | 'long_oi_usd' | 'short_oi_token' | 'short_oi_usd'
    | 'long_chg24_token' | 'long_chg24_usd' | 'short_chg24_token' | 'short_chg24_usd'
    | 'long_chg7d_token' | 'long_chg7d_usd' | 'short_chg7d_token' | 'short_chg7d_usd'
    | 'pct_24h' | 'pct_7d';

  // 'oi' = OI magnitude (long green / short red); 'chg' = signed change; 'pct' = price %.
  type Col = { key: SortKey; label: string; kind: 'oi_long' | 'oi_short' | 'chg' | 'pct' };
  let u = $derived(unit === 'token' ? 'token' : 'usd');
  let valueFmt = $derived(unit === 'token' ? fmtAmount : fmtUsd);
  let cols = $derived<Col[]>([
    { key: `long_oi_${u}` as SortKey, label: 'Long OI', kind: 'oi_long' },
    { key: `short_oi_${u}` as SortKey, label: 'Short OI', kind: 'oi_short' },
    { key: 'pct_24h', label: '24h Price Δ', kind: 'pct' },
    { key: 'pct_7d', label: '7d Price Δ', kind: 'pct' },
    { key: `long_chg24_${u}` as SortKey, label: '24h Long Δ', kind: 'chg' },
    { key: `short_chg24_${u}` as SortKey, label: '24h Short Δ', kind: 'chg' },
    { key: `long_chg7d_${u}` as SortKey, label: '7d Long Δ', kind: 'chg' },
    { key: `short_chg7d_${u}` as SortKey, label: '7d Short Δ', kind: 'chg' }
  ]);

  let sortKey = $state<SortKey>('long_oi_usd');
  let sortDir = $state<1 | -1>(-1);
  // Keep the sort on the displayed unit when sorting an OI/change column.
  let effSortKey = $derived<SortKey>(
    (sortKey.endsWith('_usd') || sortKey.endsWith('_token'))
      ? (sortKey.replace(/_(usd|token)$/, `_${u}`) as SortKey)
      : sortKey
  );

  function onSort(k: SortKey) {
    if (effSortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else {
      sortKey = k;
      sortDir = k === 'token' ? 1 : -1;
    }
  }
  function sortArrow(k: SortKey): string {
    if (effSortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }

  let sortedRows = $derived.by(() => {
    const arr = rows as Row[];
    const dir = sortDir;
    const k = effSortKey;
    return [...arr].sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      const av = a[k] as number | null;
      const bv = b[k] as number | null;
      const an = av === null || av === undefined || !isFinite(av as number);
      const bn = bv === null || bv === undefined || !isFinite(bv as number);
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      return ((av as number) - (bv as number)) * dir;
    });
  });

  function cellText(r: Row, c: Col): string {
    const v = r[c.key] as number | null;
    return c.kind === 'pct' ? fmtPct(v) : valueFmt(Number(v));
  }
  function signClass(r: Row, c: Col): string {
    if (c.kind === 'oi_long') return 'text-emerald-300';
    if (c.kind === 'oi_short') return 'text-rose-300';
    const v = r[c.key] as number | null;
    if (v === null || v === undefined || !isFinite(v) || v === 0) return 'text-zinc-500';
    return v > 0 ? 'text-emerald-400' : 'text-rose-400';
  }
</script>

<div class="h-full overflow-auto scrollbar-none text-xs" use:stopDragEvents>
  {#if error}
    <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
  {:else if loading && (rows as Row[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
  {:else if (rows as Row[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">No tokens held by this wallet set</div>
  {:else}
    <table class="w-full">
      <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
        <tr>
          <th class="text-left px-3 py-1.5 font-normal">#</th>
          <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
            onclick={() => onSort('token')}>Token{sortArrow('token')}</th>
          {#each cols as c (c.label)}
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              onclick={() => onSort(c.key)}>{c.label}{sortArrow(c.key)}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each sortedRows as r, idx (r.token)}
          <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
            <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
            <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
            {#each cols as c (c.label)}
              <td class="px-3 py-1 text-right font-mono {signClass(r, c)}">{cellText(r, c)}</td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
