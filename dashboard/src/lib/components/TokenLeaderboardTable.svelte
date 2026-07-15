<script lang="ts">
  // Token Leaderboard tableview (the `token_leaderboard` chart kind). One row
  // per token that has Binance OHLCV in the trailing window, served pre-built
  // by /api/token_leaderboard. Columns: current price, trailing-24h USD
  // volume, trailing-24h average OI (USD), and 24h / 7d price-change %.
  //
  // The server returns the full set in token order; sorting is entirely
  // client-side here (no refetch) — click a column header to re-cut. Default
  // sort is 24h price change, descending.

  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  type Row = {
    token: string;
    price: number;
    volume_24h_usd: number;
    avg_oi_24h_usd: number;
    pct_24h: number | null;
    pct_7d: number | null;
  };

  let {
    rows = [],
    loading = false,
    error = null
  }: {
    rows: Record<string, unknown>[] | Row[];
    loading?: boolean;
    error?: string | null;
  } = $props();

  function fmtPrice(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
    // Tighten precision on cheap tokens, loosen on expensive ones so a $0.0001
    // memecoin and a $60k BTC both read sensibly.
    if (abs >= 1000) return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
    if (abs >= 1) return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 2 });
    if (abs >= 0.01) return '$' + n.toFixed(4);
    return '$' + n.toPrecision(3);
  }
  function fmtUsd(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  // Change cells: leading '+' when positive, '-' when negative, em-dash when
  // the server couldn't compute a baseline (null).
  function fmtPct(n: number | null): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const sign = n > 0 ? '+' : '';
    return sign + n.toFixed(2) + '%';
  }

  type SortKey = 'token' | 'price' | 'volume_24h_usd' | 'avg_oi_24h_usd' | 'pct_24h' | 'pct_7d';
  // Default: biggest 24h gainers first.
  let sortKey = $state<SortKey>('pct_24h');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: SortKey) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else {
      sortKey = k;
      // Text sorts ascending by default; numeric columns descending.
      sortDir = k === 'token' ? 1 : -1;
    }
  }
  function sortArrow(k: SortKey): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }

  let sortedRows = $derived.by(() => {
    const arr = rows as Row[];
    const dir = sortDir;
    const k = sortKey;
    return [...arr].sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      // nulls sort last regardless of direction.
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

  const COLS: { key: SortKey; label: string }[] = [
    { key: 'price', label: 'Price' },
    { key: 'volume_24h_usd', label: '24h Volume' },
    { key: 'avg_oi_24h_usd', label: 'Avg 24h OI' },
    { key: 'pct_24h', label: '24h Δ' },
    { key: 'pct_7d', label: '7d Δ' }
  ];
</script>

<div class="h-full overflow-auto scrollbar-none text-xs" use:stopDragEvents>
  {#if error}
    <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
  {:else if loading && (rows as Row[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
  {:else if (rows as Row[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">No tokens</div>
  {:else}
    <table class="w-full freeze-first-col">
      <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
        <tr>
          <th class="text-left px-3 py-1.5 font-normal">#</th>
          <th
            class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
            onclick={() => onSort('token')}
          >Token{sortArrow('token')}</th>
          {#each COLS as c (c.key)}
            <th
              class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none"
              onclick={() => onSort(c.key)}
            >{c.label}{sortArrow(c.key)}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each sortedRows as r, idx (r.token)}
          <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
            <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
            <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-200">{fmtPrice(Number(r.price))}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(Number(r.volume_24h_usd))}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(Number(r.avg_oi_24h_usd))}</td>
            <td
              class="px-3 py-1 text-right font-mono"
              class:text-emerald-400={r.pct_24h !== null && Number(r.pct_24h) > 0}
              class:text-rose-400={r.pct_24h !== null && Number(r.pct_24h) < 0}
              class:text-zinc-500={r.pct_24h === null || Number(r.pct_24h) === 0}
            >{fmtPct(r.pct_24h as number | null)}</td>
            <td
              class="px-3 py-1 text-right font-mono"
              class:text-emerald-400={r.pct_7d !== null && Number(r.pct_7d) > 0}
              class:text-rose-400={r.pct_7d !== null && Number(r.pct_7d) < 0}
              class:text-zinc-500={r.pct_7d === null || Number(r.pct_7d) === 0}
            >{fmtPct(r.pct_7d as number | null)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
