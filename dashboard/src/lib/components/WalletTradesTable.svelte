<script lang="ts">
  // Latest individual trades (fills) for the HL wallet detail page. Owns its own fetch
  // (/api/hyperliquid/wallet_fills) + loading state so a long window can't block the
  // rest of the page. In RANGE mode it shows the selected date range; otherwise a
  // lookback selector (default 1d = last 24h) bounds the window — longer picks (up to
  // 90d) are opt-in and show a loading indicator.
  //
  // Two view modes:
  //   Trades    — the raw fills (this component's original behaviour).
  //   Aggregate — exactly the Trading Pit "Overview": one row per token with the 8 (+2
  //               flip) action categories as $ (count) + Net Pos Change, but scoped to
  //               THIS wallet (via /hyperliquid/trading_pit?wallet=…&mode=overview). Same
  //               table chrome as Trades mode — only the columns differ.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { fmtTzDateTime } from '$lib/stores/timezone.svelte';

  export type TradeRow = {
    time: number; token: string; dir: string; side: string;
    price: number; size: number; value: number; closed_pnl: number; fee: number;
  };
  type OverviewRow = { token: string } & Record<string, [number, number] | number | string>;

  let {
    wallet = '',
    rangeMode = false,
    rangeSince = '',
    rangeUntil = '',
    initialToken = ''
  }: {
    wallet?: string;
    rangeMode?: boolean;
    rangeSince?: string; // ISO (range mode only)
    rangeUntil?: string; // ISO (range mode only)
    initialToken?: string; // seed the token filter (e.g. from ?token=)
  } = $props();

  const LOOKBACKS: Array<{ v: string; secs: number }> = [
    { v: '15m', secs: 900 }, { v: '1h', secs: 3600 }, { v: '4h', secs: 14400 },
    { v: '1d', secs: 86400 }, { v: '3d', secs: 259200 }, { v: '7d', secs: 604800 },
    { v: '30d', secs: 2592000 }, { v: '90d', secs: 7776000 }
  ];
  let lookback = $state('1h');
  let viewMode = $state<'trades' | 'aggregate'>('trades');

  let trades = $state<TradeRow[]>([]);
  let overviewRows = $state<OverviewRow[]>([]);
  let flipSplit = $state(false);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let tokenFilter = $state(initialToken ?? '');
  let ctl: AbortController | null = null;

  const shown = $derived(tokenFilter ? trades.filter((t) => t.token === tokenFilter) : trades);
  const shownOv = $derived(tokenFilter ? overviewRows.filter((r) => r.token === tokenFilter) : overviewRows);
  // Distinct traded tokens in the window, plus the current pick (so it survives a reload
  // where that token has no fills), sorted for the selector.
  const tokenOpts = $derived(
    [...new Set([
      ...(tokenFilter ? [tokenFilter] : []),
      ...trades.map((t) => t.token),
      ...overviewRows.map((r) => r.token)
    ])].sort()
  );
  const isBuy = (t: TradeRow) => t.side === 'B';

  // Window [since, until] as ISO — shared by both modes (range picks it, else lookback).
  function windowIso(rm: boolean, rs: string, ru: string, lb: string): { since: string; until: string } {
    if (rm && rs && ru) return { since: rs, until: ru };
    const secs = LOOKBACKS.find((l) => l.v === lb)?.secs ?? 3600;
    const now = Date.now();
    return { since: new Date(now - secs * 1000).toISOString(), until: new Date(now).toISOString() };
  }

  async function load(w: string, vm: string, rm: boolean, rs: string, ru: string, lb: string) {
    if (!w) { trades = []; overviewRows = []; return; }
    ctl?.abort();
    const c = new AbortController();
    ctl = c;
    loading = true;
    error = null;
    try {
      const { since, until } = windowIso(rm, rs, ru, lb);
      if (vm === 'aggregate') {
        // Trading Pit Overview scoped to this wallet. Always send since/until (the
        // endpoint's `lookback` list caps at 4h; since/until bypasses that).
        const qs = new URLSearchParams({
          wallet: w, all_tokens: '1', mode: 'overview', since, until
        });
        const res = await fetch(`/api/hyperliquid/trading_pit?${qs}`, { signal: c.signal });
        if (!res.ok) throw new Error(`aggregate ${res.status}`);
        const body = await res.json();
        overviewRows = (body.tokens ?? []) as OverviewRow[];
        flipSplit = !!body.flip_split;
      } else {
        const res = await fetch(
          `/api/hyperliquid/wallet_fills?wallet=${w}&since=${since}&until=${until}&limit=500`,
          { signal: c.signal }
        );
        if (!res.ok) throw new Error(`trades ${res.status}`);
        const body = await res.json();
        trades = (body.trades ?? []) as TradeRow[];
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        error = (e as Error).message;
        trades = [];
        overviewRows = [];
      }
    } finally {
      if (ctl === c) loading = false;
    }
  }
  // Reload on wallet / view mode / range window / lookback change.
  $effect(() => {
    load(wallet, viewMode, rangeMode, rangeSince, rangeUntil, lookback);
  });
  const refresh = () => load(wallet, viewMode, rangeMode, rangeSince, rangeUntil, lookback);

  function fmtUsd(n: number): string {
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtPrice(n: number): string {
    return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toPrecision(5);
  }

  // ── Overview (Aggregate) column model — identical set/labels/colors to Trading Pit. ──
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
  const typeCls = (t: string) => TYPE_META[t]?.cls ?? 'text-zinc-400';
  const OV_BASE = ['open_long', 'open_short', 'inc_long', 'dec_long', 'inc_short', 'dec_short', 'close_long', 'close_short'];
  const ovCols = $derived(flipSplit ? OV_BASE : [...OV_BASE, 'flip_ls', 'flip_sl']);
  const cellOf = (r: OverviewRow, k: string): [number, number] => {
    const v = r[k];
    return Array.isArray(v) ? (v as [number, number]) : [0, 0];
  };
  // Net position change per token (directional flow, excl. opens/closes/flips):
  // increased longs + decreased shorts − increased shorts − decreased longs.
  const netValue = (r: OverviewRow) =>
    cellOf(r, 'inc_long')[0] + cellOf(r, 'dec_short')[0] - cellOf(r, 'inc_short')[0] - cellOf(r, 'dec_long')[0];
  // Parenthesis = number of fills making up the net change (the inc/dec fill count),
  // not distinct wallets — this view is already a single wallet. Summed from the four
  // inc/dec category counts the net is built from.
  const netCount = (r: OverviewRow) =>
    cellOf(r, 'inc_long')[1] + cellOf(r, 'dec_short')[1] + cellOf(r, 'inc_short')[1] + cellOf(r, 'dec_long')[1];

  // ── Overview client-side sort (default: Net Pos Change desc). ──
  let ovSortKey = $state<string>('net');
  let ovSortDir = $state<1 | -1>(-1);
  function onOvSort(k: string) {
    if (ovSortKey === k) ovSortDir = ovSortDir === 1 ? -1 : 1;
    else { ovSortKey = k; ovSortDir = -1; }
  }
  const ovArrow = (k: string) => (ovSortKey !== k ? '' : ovSortDir === 1 ? ' ↑' : ' ↓');
  const sortedOv = $derived.by(() => {
    const arr = [...shownOv];
    const k = ovSortKey, dir = ovSortDir;
    arr.sort((a, b) => {
      if (k === 'token') return String(a.token).localeCompare(String(b.token)) * dir;
      if (k === 'net') return (netValue(a) - netValue(b)) * dir;
      return (cellOf(a, k)[0] - cellOf(b, k)[0]) * dir;
    });
    return arr;
  });
</script>

<div class="h-full flex flex-col text-sm border border-zinc-800 rounded-lg overflow-hidden" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-200 font-medium">Trades</span>
    <div class="flex rounded border border-zinc-700 overflow-hidden text-[11px]" title="Trades = raw fills · Aggregate = per-token action summary (like Trading Pit Overview)">
      {#each [['trades', 'Trades'], ['aggregate', 'Aggregate']] as [v, lbl] (v)}
        <button type="button" onclick={() => (viewMode = v as 'trades' | 'aggregate')}
          class="px-2 py-0.5 {viewMode === v ? 'bg-zinc-700 text-zinc-100' : 'bg-zinc-900 text-zinc-400 hover:bg-zinc-800'}">{lbl}</button>
      {/each}
    </div>
    {#if loading}
      <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading…"></span>
    {/if}
    {#if !rangeMode}
      <select
        class="bg-zinc-900 border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-200 focus:outline-none focus:border-zinc-500"
        bind:value={lookback} title="How far back to load trades">
        {#each LOOKBACKS as l (l.v)}<option value={l.v}>{l.v}</option>{/each}
      </select>
    {:else}
      <span class="text-zinc-500 text-xs">range</span>
    {/if}
    <select
      class="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:border-zinc-500"
      bind:value={tokenFilter} title="Filter by token">
      <option value="">All tokens</option>
      {#each tokenOpts as tok (tok)}<option value={tok}>{tok}</option>{/each}
    </select>
    <button
      type="button"
      onclick={refresh}
      disabled={loading}
      class="text-[11px] px-2 py-0.5 rounded border border-zinc-700 bg-zinc-800/50 text-zinc-300 hover:bg-zinc-700/50 disabled:opacity-40 disabled:cursor-default whitespace-nowrap"
      title="Refresh"
    >↻ Refresh</button>
    <span class="ml-auto text-zinc-500 text-xs whitespace-nowrap">
      {viewMode === 'aggregate' ? `${shownOv.length} tokens` : `${shown.length} fills`}
    </span>
  </div>

  <div class="flex-1 overflow-auto scrollbar-none {loading ? 'opacity-50' : ''}">
    {#if error}
      <div class="h-full flex items-center justify-center text-rose-400 text-center px-4 py-8">{error}</div>
    {:else if loading && trades.length === 0 && overviewRows.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">Loading…</div>
    {:else if viewMode === 'aggregate'}
      {#if shownOv.length === 0}
        <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">
          {overviewRows.length === 0 ? 'No trades in this window.' : 'No trades match the filter.'}
        </div>
      {:else}
        <table class="w-full">
          <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
            <tr>
              <th class="text-left px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onOvSort('token')}>Token{ovArrow('token')}</th>
              <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none whitespace-nowrap" onclick={() => onOvSort('net')} title="Net position change: increased longs + decreased shorts − increased shorts − decreased longs (directional flow, excludes opens/closes)">Net Pos Change{ovArrow('net')}</th>
              {#each ovCols as c (c)}
                <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none whitespace-nowrap {typeCls(c)}" onclick={() => onOvSort(c)} title={typeLabel(c)}>{typeLabel(c)}{ovArrow(c)}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each sortedOv as r (r.token)}
              <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
                <td class="px-3 py-1 font-medium text-zinc-100">{r.token}</td>
                <td class="px-3 py-1 text-right font-mono tabular-nums whitespace-nowrap {netValue(r) > 0 ? 'text-emerald-400' : netValue(r) < 0 ? 'text-rose-400' : 'text-zinc-600'}">
                  {netCount(r) ? `${fmtUsd(netValue(r))} (${netCount(r)})` : '—'}
                </td>
                {#each ovCols as c (c)}
                  {@const cell = cellOf(r, c)}
                  <td class="px-3 py-1 text-right font-mono tabular-nums whitespace-nowrap {cell[1] ? 'text-zinc-200' : 'text-zinc-700'}">
                    {cell[1] ? `${fmtUsd(cell[0])} (${cell[1]})` : '—'}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {:else if shown.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500 text-center px-4 py-8">
        {trades.length === 0 ? 'No trades in this window.' : 'No trades match the filter.'}
      </div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left px-3 py-1.5 font-normal">Time</th>
            <th class="text-left px-3 py-1.5 font-normal">Token</th>
            <th class="text-left px-3 py-1.5 font-normal">Direction</th>
            <th class="text-right px-3 py-1.5 font-normal">Size</th>
            <th class="text-right px-3 py-1.5 font-normal">Price</th>
            <th class="text-right px-3 py-1.5 font-normal">Realized PnL</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as t, i (i)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-300 tabular-nums whitespace-nowrap">{fmtTzDateTime(t.time)}</td>
              <td class="px-3 py-1 text-zinc-200">{t.token}</td>
              <td class="px-3 py-1 whitespace-nowrap">
                <span class="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border {isBuy(t)
                  ? 'border-emerald-800 text-emerald-400'
                  : 'border-rose-800 text-rose-400'}">{t.dir}</span>
              </td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-300">{fmtUsd(t.value)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums text-zinc-300">{fmtPrice(t.price)}</td>
              <td class="px-3 py-1 text-right font-mono tabular-nums {t.closed_pnl > 0 ? 'text-emerald-400' : t.closed_pnl < 0 ? 'text-rose-400' : 'text-zinc-500'}">{t.closed_pnl ? fmtUsd(t.closed_pnl) : '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
