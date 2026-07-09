<script lang="ts">
  // Trading Pit table: a wallet group's classified HL-perp fills over a short window.
  // Normal/Aggregate → one row per (aggregated) fill; Overview → one row per token with
  // the 8 (+2 flip) action categories as $ (count). Columns sortable; server-side filters
  // (min_size/side/type/token) live in the header and call back to re-fetch.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';
  import { fmtTzTime } from '$lib/stores/timezone.svelte';

  type FillRow = {
    time?: number; wallet: string; token: string; type: string; side: string;
    price?: number; value: number; closed_pnl?: number; count?: number; categories?: string[];
  };
  type OverviewRow = { token: string } & Record<string, [number, number]>;
  type Filters = { minSize: number; side: string; type: string; token: string };

  let {
    mode = 'normal',
    rows = [],
    overviewRows = [],
    flipSplit = false,
    loading = false,
    error = null,
    selectedTokens = [],
    allTokens = false,
    availableTokens = [],
    timeFormat = 'relative',
    filters = { minSize: 0, side: '', type: '', token: '' },
    onFilter = (_p: Partial<Filters>) => {},
    onTokenClick = (_t: string) => {}
  }: {
    mode?: 'normal' | 'aggregate' | 'overview';
    rows?: FillRow[];
    overviewRows?: OverviewRow[];
    flipSplit?: boolean;
    loading?: boolean;
    error?: string | null;
    selectedTokens?: string[];
    allTokens?: boolean;
    availableTokens?: string[];
    timeFormat?: 'relative' | 'standard';
    filters?: Filters;
    onFilter?: (p: Partial<Filters>) => void;
    onTokenClick?: (t: string) => void;
  } = $props();

  // Time cell: relative ('3m ago', aggregate = median fill time) or standard clock.
  function fmtTime(ts: number | undefined): string {
    if (!ts) return '';
    if (timeFormat === 'standard') return fmtTzTime(ts);
    let s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60); s -= m * 60;
    const parts: string[] = [];
    if (d) parts.push(`${d}d`);
    if (h) parts.push(`${h}h`);
    if (m) parts.push(`${m}m`);
    if (!parts.length) parts.push(`${s}s`);
    return `${parts.slice(0, 2).join(' ')} ago`;
  }

  // type → label + color
  const TYPE_META: Record<string, { label: string; cls: string }> = {
    open_long: { label: 'Opened Long', cls: 'text-emerald-400' },
    inc_long: { label: 'Increased Long', cls: 'text-emerald-400' },
    dec_long: { label: 'Decreased Long', cls: 'text-amber-400' },
    close_long: { label: 'Closed Long', cls: 'text-rose-400' },
    open_short: { label: 'Opened Short', cls: 'text-rose-400' },
    inc_short: { label: 'Increased Short', cls: 'text-rose-400' },
    dec_short: { label: 'Decreased Short', cls: 'text-amber-400' },
    close_short: { label: 'Closed Short', cls: 'text-emerald-400' },
    flip_ls: { label: 'Flip L→S', cls: 'text-fuchsia-400' },
    flip_sl: { label: 'Flip S→L', cls: 'text-fuchsia-400' }
  };
  const typeLabel = (t: string) => TYPE_META[t]?.label ?? t;
  const typeCls = (t: string) => TYPE_META[t]?.cls ?? 'text-zinc-300';
  // Overview columns in the user's order + flip cols (Separate only).
  const OV_BASE = ['open_long', 'open_short', 'inc_long', 'dec_long', 'inc_short', 'dec_short', 'close_long', 'close_short'];
  const ovCols = $derived(flipSplit ? OV_BASE : [...OV_BASE, 'flip_ls', 'flip_sl']);
  // Type filter is side-agnostic (Side is its own filter): the 4 action categories.
  const TYPE_FILTER_LABELS: Record<string, string> = {
    opened: 'Opened', increased: 'Increased', decreased: 'Decreased', closed: 'Closed'
  };

  function fmtUsd(n: number): string {
    const a = Math.abs(n), s = n < 0 ? '-' : '';
    if (a >= 1e9) return s + '$' + (a / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return s + '$' + (a / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return s + '$' + (a / 1e3).toFixed(1) + 'K';
    return s + '$' + a.toFixed(0);
  }
  const num = (e: Event) => Math.max(0, Number((e.currentTarget as HTMLInputElement).value) || 0);
  // Token-narrow dropdown: All-tokens mode → every token the group traded (from the
  // server); otherwise the selected capsules. Falls back to whatever is present.
  const dataTokens = $derived(
    [...new Set([
      ...(rows as FillRow[]).map((r) => r.token),
      ...(overviewRows as OverviewRow[]).map((r) => r.token)
    ])].sort()
  );
  const tokenOpts = $derived(
    allTokens
      ? (availableTokens.length ? availableTokens : dataTokens)
      : (selectedTokens.length ? selectedTokens : dataTokens)
  );

  // ── client-side sort (fills modes) ──
  let sortKey = $state<string>('value');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  const arrow = (k: string) => (sortKey !== k ? '' : sortDir === 1 ? ' ↑' : ' ↓');
  const fillSv = (r: FillRow, k: string): number | string => {
    if (k === 'wallet') return r.wallet;
    if (k === 'token') return r.token;
    if (k === 'type') return typeLabel(r.type);
    if (k === 'side') return r.side;
    if (k === 'time') return r.time ?? 0;
    if (k === 'price') return r.price ?? 0;
    if (k === 'pnl') return r.closed_pnl ?? 0;
    if (k === 'count') return r.count ?? 0;
    return r.value;
  };
  let sortedFills = $derived.by(() => {
    const arr = [...(rows as FillRow[])];
    const k = sortKey, dir = sortDir;
    arr.sort((a, b) => {
      const av = fillSv(a, k), bv = fillSv(b, k);
      if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * dir;
      return (av - bv) * dir;
    });
    return arr;
  });

  // Net position change per token (directional flow, excl. opens/closes/flips):
  // increased longs + decreased shorts − increased shorts − decreased longs.
  const netValue = (r: OverviewRow) =>
    (r.inc_long?.[0] ?? 0) + (r.dec_short?.[0] ?? 0) - (r.inc_short?.[0] ?? 0) - (r.dec_long?.[0] ?? 0);
  // distinct wallets that increased/decreased (from the server), NOT #fills.
  const netCount = (r: OverviewRow) => ((r as unknown as { net_wallets?: number }).net_wallets ?? 0);

  // Overview sort (by a chosen column's value).
  let ovSortKey = $state<string>('token');
  let ovSortDir = $state<1 | -1>(-1);
  function onOvSort(k: string) {
    if (ovSortKey === k) ovSortDir = ovSortDir === 1 ? -1 : 1;
    else { ovSortKey = k; ovSortDir = -1; }
  }
  const ovArrow = (k: string) => (ovSortKey !== k ? '' : ovSortDir === 1 ? ' ↑' : ' ↓');
  let sortedOv = $derived.by(() => {
    const arr = [...(overviewRows as OverviewRow[])];
    const k = ovSortKey, dir = ovSortDir;
    arr.sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      if (k === 'net') return (netValue(a) - netValue(b)) * dir;
      const av = (a[k]?.[0] ?? 0), bv = (b[k]?.[0] ?? 0);
      return (av - bv) * dir;
    });
    return arr;
  });

  const selCls = 'bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-200 focus:outline-none focus:border-zinc-500';
</script>

<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- filter bar -->
  <div class="flex items-center flex-wrap gap-2 px-3 py-1.5 border-b border-zinc-800 text-zinc-400">
    <label class="flex items-center gap-1 text-[11px]" title="Min fill/aggregate size ($)">≥$
      <input type="number" min="0" step="100" class={selCls + ' w-20'} value={filters.minSize}
        onchange={(e) => onFilter({ minSize: num(e) })} /></label>
    <label class="flex items-center gap-1 text-[11px]" title="Side">Side
      <select class={selCls} value={filters.side} onchange={(e) => onFilter({ side: e.currentTarget.value })}>
        <option value="">All</option><option value="long">Long</option><option value="short">Short</option>
      </select></label>
    {#if mode !== 'overview'}
      <label class="flex items-center gap-1 text-[11px]" title="Fill type">Type
        <select class={selCls} value={filters.type} onchange={(e) => onFilter({ type: e.currentTarget.value })}>
          <option value="">All</option>
          {#each Object.entries(TYPE_FILTER_LABELS) as [v, lbl] (v)}<option value={v}>{lbl}</option>{/each}
        </select></label>
    {/if}
    <label class="flex items-center gap-1 text-[11px]" title="Filter to one selected token">Token
      <select class={selCls} value={filters.token} onchange={(e) => onFilter({ token: e.currentTarget.value })}>
        <option value="">All</option>
        {#each tokenOpts as t (t)}<option value={t}>{t}</option>{/each}
      </select></label>
    <span class="ml-auto text-[11px] text-zinc-500">
      {mode === 'overview' ? `${overviewRows.length} tokens` : `${rows.length} ${mode === 'aggregate' ? 'aggregates' : 'fills'}`}
    </span>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-60' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 px-4 text-center">{error}</div>
    {:else if loading && rows.length === 0 && overviewRows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">Loading…</div>
    {:else if mode === 'overview'}
      {#if overviewRows.length === 0}
        <div class="h-full flex items-center justify-center text-zinc-500">No fills in window</div>
      {:else}
        <table class="w-full">
          <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
            <tr>
              <th class="text-left px-2 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onOvSort('token')}>Token{ovArrow('token')}</th>
              <th class="text-right px-2 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none whitespace-nowrap" onclick={() => onOvSort('net')} title="Net position change: increased longs + decreased shorts − increased shorts − decreased longs (directional flow, excludes opens/closes)">Net Pos Change{ovArrow('net')}</th>
              {#each ovCols as c (c)}
                <th class="text-right px-2 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none whitespace-nowrap {typeCls(c)}" onclick={() => onOvSort(c)} title={typeLabel(c)}>{typeLabel(c)}{ovArrow(c)}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each sortedOv as r (r.token)}
              <tr class="border-b border-zinc-900 hover:bg-zinc-900/50 cursor-pointer" onclick={() => onTokenClick(r.token)} title="Show the wallets that traded {r.token}">
                <td class="px-2 py-1 font-medium text-zinc-100">{r.token}</td>
                <td class="px-2 py-1 text-right font-mono tabular-nums whitespace-nowrap {netValue(r) > 0 ? 'text-emerald-400' : netValue(r) < 0 ? 'text-rose-400' : 'text-zinc-600'}">
                  {netCount(r) ? `${fmtUsd(netValue(r))} (${netCount(r)})` : '—'}
                </td>
                {#each ovCols as c (c)}
                  {@const cell = r[c] ?? [0, 0]}
                  <td class="px-2 py-1 text-right font-mono tabular-nums whitespace-nowrap {cell[1] ? 'text-zinc-200' : 'text-zinc-700'}">
                    {cell[1] ? `${fmtUsd(cell[0])} (${cell[1]})` : '—'}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {:else if rows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No fills in window</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('time')} title={mode === 'aggregate' ? 'Median time of the aggregated fills' : 'Fill time'}>Time{arrow('time')}</th>
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('wallet')} title="Sort by wallet (groups a wallet's fills together)">Wallet{arrow('wallet')}</th>
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('token')}>Token{arrow('token')}</th>
            <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('type')}>Type{arrow('type')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('value')}>Size{arrow('value')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('price')} title={mode === 'aggregate' ? 'Size-weighted average price' : 'Fill price'}>Price{arrow('price')}</th>
            {#if mode === 'normal'}
              <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('pnl')}>PnL{arrow('pnl')}</th>
            {:else}
              <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('count')}>Fills{arrow('count')}</th>
            {/if}
          </tr>
        </thead>
        <tbody>
          {#each sortedFills as r, i (i)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono tabular-nums text-zinc-400 whitespace-nowrap">{fmtTime(r.time)}</td>
              <td class="px-3 py-1"><WalletAddress address={r.wallet} auxKind="wallet" token={r.token} tags={r.categories ?? []} /></td>
              <td class="px-3 py-1 text-zinc-200">{r.token}</td>
              <td class="px-3 py-1 whitespace-nowrap {typeCls(r.type)}">{typeLabel(r.type)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-200">{fmtUsd(r.value)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400">{r.price ? r.price.toPrecision(5) : ''}</td>
              {#if mode === 'normal'}
                <td class="px-3 py-1 text-right font-mono tabular-nums {(r.closed_pnl ?? 0) > 0 ? 'text-emerald-400' : (r.closed_pnl ?? 0) < 0 ? 'text-rose-400' : 'text-zinc-600'}">{r.closed_pnl ? fmtUsd(r.closed_pnl) : '—'}</td>
              {:else}
                <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-400">{r.count ?? ''}</td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
