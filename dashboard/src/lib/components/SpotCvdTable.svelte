<script lang="ts">
  // Spot CVD tableview (the `spot_cvd_table` chart kind). One row per Binance-
  // spot token, served pre-built by /api/spot_cvd_leaderboard. Ranks tokens by
  // cumulative spot CVD (taker buy − sell) over the chosen lookback, normalised
  // vs average daily volume.
  //
  // The server returns the full set; sorting + the 10/30/All limit are entirely
  // client-side here. Click a column header (or use the Sort dropdown) to re-cut.
  // `unit` ($/token, from the chart toolbar) flips the Avg-Vol + CVD-Vol columns;
  // the two ratio % columns always show. Default sort: Cum $ CVD, descending.

  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  // Single-period rows carry cvd_*/avg_volume_*/ratio_*; multi ('All') rows
  // carry per-period cvd_{usd,token}_{1,7,14} + ratio_usd_{1,7,14}. Access is
  // generic (record), so one Row type with optional fields suffices.
  type Row = Record<string, number | null> & { token: string };

  let {
    rows = [],
    loading = false,
    error = null,
    unit = 'usd',
    multi = false,
    lookbackLabel = 'Lookback'
  }: {
    rows: Record<string, unknown>[] | Row[];
    loading?: boolean;
    error?: string | null;
    unit?: 'usd' | 'token';
    multi?: boolean;
    lookbackLabel?: string;
  } = $props();

  function fmtPrice(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n);
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

  type SortKey = string;

  let u = $derived(unit === 'token' ? 'token' : 'usd');
  let valueFmt = $derived(unit === 'token' ? fmtAmount : fmtUsd);
  let sortDir = $state<1 | -1>(-1);
  let limit = $state<'10' | '30' | 'all'>('30');
  let search = $state('');

  // Fuzzy token match: query chars appear in order in the token (case-insensitive).
  function fuzzy(q: string, t: string): boolean {
    q = q.trim().toLowerCase();
    if (!q) return true;
    t = t.toLowerCase();
    let i = 0;
    for (const ch of t) { if (ch === q[i]) i++; if (i === q.length) return true; }
    return false;
  }
  // Default ranking differs by mode; reset when the mode flips so a stale key
  // (e.g. 'cvd_usd_14' from multi) doesn't sort by undefined in single mode.
  let sortKey = $state<SortKey>('cvd_usd');
  $effect(() => {
    sortKey = multi ? 'cvd_usd_14' : 'cvd_usd';
    sortDir = -1;
  });

  type Criteria = 'cvd_usd' | 'ratio_usd' | 'ratio_token';
  function applyCriteria(c: Criteria) {
    sortKey = c;
  }

  type Col = { key: SortKey; label: string; kind: 'price' | 'value' | 'pct' };
  let cols = $derived<Col[]>(multi
    ? [
        { key: 'price', label: 'Price', kind: 'price' },
        { key: 'pct_24h', label: '24h Δ', kind: 'pct' },
        { key: `cvd_${u}_1h`, label: '1h CVD', kind: 'value' },
        { key: `cvd_${u}_4h`, label: '4h CVD', kind: 'value' },
        { key: `cvd_${u}_1`, label: '1d CVD', kind: 'value' },
        { key: `cvd_${u}_7`, label: '7d CVD', kind: 'value' },
        { key: `cvd_${u}_14`, label: '14d CVD', kind: 'value' },
        { key: 'ratio_usd_1h', label: '1h $CVD/vol%', kind: 'pct' },
        { key: 'ratio_usd_4h', label: '4h $CVD/vol%', kind: 'pct' },
        { key: 'ratio_usd_1', label: '1d $CVD/vol%', kind: 'pct' },
        { key: 'ratio_usd_7', label: '7d $CVD/vol%', kind: 'pct' },
        { key: 'ratio_usd_14', label: '14d $CVD/vol%', kind: 'pct' }
      ]
    : [
        { key: 'price', label: 'Price', kind: 'price' },
        { key: `avg_volume_${u}`, label: 'Avg Vol', kind: 'value' },
        { key: `cvd_${u}`, label: 'CVD Vol', kind: 'value' },
        { key: 'pct_24h', label: '24h Δ', kind: 'pct' },
        { key: 'pct_lookback', label: `${lookbackLabel} Δ`, kind: 'pct' },
        { key: 'ratio_token', label: 'CVD/Vol %', kind: 'pct' },
        { key: 'ratio_usd', label: '$CVD/$Vol %', kind: 'pct' }
      ]);

  function onSort(k: SortKey) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else {
      sortKey = k;
      sortDir = k === 'token' ? 1 : -1;
    }
  }
  function sortArrow(k: SortKey): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }

  let sortedRows = $derived.by(() => {
    const arr = (rows as Row[]).filter((r) => fuzzy(search, String(r.token)));
    const dir = sortDir;
    const k = sortKey;
    const s = [...arr].sort((a, b) => {
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
    return limit === 'all' ? s : s.slice(0, Number(limit));
  });

  function cellText(r: Row, c: Col): string {
    const v = r[c.key] as number | null;
    if (c.kind === 'price') return fmtPrice(Number(v));
    if (c.kind === 'value') return valueFmt(Number(v));
    return fmtPct(v);
  }
  function signClass(r: Row, c: Col): string {
    if (c.kind === 'price') return 'text-zinc-200';
    const v = r[c.key] as number | null;
    if (v === null || v === undefined || !isFinite(v) || v === 0) return 'text-zinc-500';
    return v > 0 ? 'text-emerald-400' : 'text-rose-400';
  }

  const selClass =
    'bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-[11px] text-zinc-200 hover:border-zinc-600 focus:outline-none';
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- In-table controls: sort criteria + direction + row limit. -->
  <div class="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    {#if !multi}
      <span class="text-[11px]">Sort</span>
      <select class={selClass} value={sortKey === 'ratio_usd' || sortKey === 'ratio_token' ? sortKey : 'cvd_usd'}
        onchange={(e) => applyCriteria(e.currentTarget.value as Criteria)} title="Ranking metric">
        <option value="cvd_usd">Cum $ CVD</option>
        <option value="ratio_usd">$CVD ÷ $AvgVol</option>
        <option value="ratio_token">CVD ÷ AvgVol</option>
      </select>
    {:else}
      <span class="text-[11px]">1h / 4h / 1d / 7d / 14d CVD · click a header to sort</span>
    {/if}
    <button class={selClass} onclick={() => (sortDir = sortDir === 1 ? -1 : 1)}
      title="Toggle ascending / descending">{sortDir === 1 ? 'Asc ↑' : 'Desc ↓'}</button>
    <input class={selClass + ' w-24'} placeholder="Token…" bind:value={search}
      title="Fuzzy filter by token name" />
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
      <table class="w-full freeze-first-col">
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
</div>
