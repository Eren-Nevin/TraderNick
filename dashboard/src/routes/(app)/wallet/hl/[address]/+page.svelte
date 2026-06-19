<script lang="ts">
  // Hyperliquid wallet detail page (/wallet/hl/<address>). Glassnode-style:
  // a PnL equity curve + headline stats + a positions table, with a 1-day
  // "as of" slider. PnL curve is GLOBAL (all tokens) and always full-history
  // with a marker at the selected day; the slider drives the positions table
  // and the as-of stat cards. Positions are HYBRID: today → HL live API
  // (exact, all tokens, account value); past days → our stored snapshots.

  import WalletPnlChart from '$lib/components/WalletPnlChart.svelte';
  import WalletPositionsTable, {
    type PositionRow
  } from '$lib/components/WalletPositionsTable.svelte';
  import {
    DAY_SLIDER_MAX_BACK,
    backToIso,
    isoToUnix,
    isToday
  } from '$lib/daySlider';
  import { arkhamUrl, coinglassHlUrl } from '$lib/arkham';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  const address = $derived(data.address);

  type PnlPoint = {
    time: number; realized: number; total: number;
    realized_day: number; unrealized: number;
  };
  type PnlStats = {
    realized_pnl: number; unrealized_pnl: number;
    sharpe: number; volatility: number;
  };

  // ── State ──────────────────────────────────────────────────────────
  // Slider position is the source of truth (bind:value) so the drag never
  // fights a re-asserted one-way value. 0 = oldest (left), MAX = today (right).
  let sliderPos = $state(DAY_SLIDER_MAX_BACK);
  let pnlSeries = $state<PnlPoint[]>([]);
  let pnlStats = $state<PnlStats | null>(null);
  let pnlLoading = $state(true);
  let pnlError = $state<string | null>(null);
  let pnlMode = $state<'total' | 'realized'>('total');

  let positions = $state<PositionRow[]>([]);
  let posLoading = $state(false);
  let posError = $state<string | null>(null);
  let accountValue = $state<number | null>(null);

  let copied = $state(false);

  // ── Derived ────────────────────────────────────────────────────────
  const MAX_BACK = DAY_SLIDER_MAX_BACK;
  const snapshotIso = $derived(backToIso(MAX_BACK - sliderPos));
  const live = $derived(isToday(snapshotIso));
  const selectedUnix = $derived(isoToUnix(snapshotIso));

  const chartData = $derived(
    pnlSeries.map((p) => ({ time: p.time, value: pnlMode === 'total' ? p.total : p.realized }))
  );

  // Last daily point at or before the selected day → as-of realized/unrealized.
  const asOf = $derived.by(() => {
    let pick: PnlPoint | null = null;
    for (const p of pnlSeries) {
      if (p.time <= selectedUnix) pick = p;
      else break;
    }
    return pick;
  });

  const realizedAsOf = $derived(asOf ? asOf.realized : 0);
  const oiUsd = $derived(positions.reduce((s, p) => s + Math.abs(p.size_usd), 0));
  // Prefer the positions-source unrealized (live or snapshot); fall back to the
  // EOD series value when there are no position rows for the day.
  const unrealAsOf = $derived(
    positions.length ? positions.reduce((s, p) => s + p.unrealized_pnl, 0) : asOf ? asOf.unrealized : 0
  );
  const totalAsOf = $derived(realizedAsOf + unrealAsOf);

  // ── Formatters ─────────────────────────────────────────────────────
  function fmtUsd(n: number | null | undefined): string {
    if (n === null || n === undefined || !isFinite(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function truncate(addr: string): string {
    if (!addr || addr.length < 14) return addr;
    return addr.slice(0, 8) + '…' + addr.slice(-6);
  }
  function pnlClass(n: number): string {
    return n > 0 ? 'text-emerald-400' : n < 0 ? 'text-rose-400' : 'text-zinc-300';
  }
  async function copyAddr() {
    try {
      await navigator.clipboard.writeText(address);
      copied = true;
      setTimeout(() => (copied = false), 1200);
    } catch { /* no-op */ }
  }

  // ── Mappers (live API shape vs stored-snapshot shape → PositionRow) ──
  function mapLive(p: Record<string, unknown>): PositionRow {
    // HL's return_on_equity is leverage-true (PnL / margin). Past-day ROE can't
    // be (margin isn't stored) so we report return on entry notional there;
    // de-leverage the live value here (÷ leverage) so both modes mean the same.
    const lev = Number(p.leverage_value) || 0;
    const roeLev = Number(p.return_on_equity);
    return {
      token: String(p.token), side: p.side as 'long' | 'short',
      amount: Number(p.amount), size_usd: Number(p.size),
      unrealized_pnl: Number(p.unrealized_pnl),
      entry_px: p.entry_px as number, liquidation_px: p.liquidation_px as number,
      roe: lev ? roeLev / lev : roeLev, funding: p.cum_funding_since_open as number,
      leverage: p.leverage_value as number, leverage_type: p.leverage_type as string
    };
  }
  function mapHist(p: Record<string, unknown>): PositionRow {
    const amount = Number(p.amount);
    const size_usd = Number(p.size_usd);
    const unrealized_pnl = Number(p.unrealized_pnl);
    const entry_px = p.entry_px != null ? Number(p.entry_px) : null;
    // ROE we can't get leverage-true for past days (margin isn't stored), so
    // report the return on the position's entry notional (= price move %).
    // entry_notional = |amount| × entry; equivalently size_usd ∓ unrealized.
    const entryNotional = entry_px ? Math.abs(amount) * entry_px : 0;
    const roe = entryNotional ? unrealized_pnl / entryNotional : null;
    return {
      token: String(p.token), side: p.side as 'long' | 'short',
      amount, size_usd, unrealized_pnl, entry_px, roe,
      funding: p.funding != null ? Number(p.funding) : null
    };
  }

  // ── Fetching ───────────────────────────────────────────────────────
  async function loadPnl() {
    pnlLoading = true; pnlError = null;
    try {
      const since = backToIso(MAX_BACK);
      const until = backToIso(0);
      const res = await fetch(
        `/api/hyperliquid/wallet_pnl?wallet=${address}&since=${since}&until=${until}`
      );
      if (!res.ok) throw new Error(`PnL ${res.status}`);
      const body = await res.json();
      pnlSeries = body.series ?? [];
      pnlStats = body.stats ?? null;
    } catch (e) {
      pnlError = (e as Error).message;
    } finally {
      pnlLoading = false;
    }
  }

  // Monotonic request token: only the latest loadPositions call may write
  // state. Without it the slow live-API fetch (mount) can resolve AFTER a fast
  // ClickHouse fetch for a past day and clobber it back to today's book.
  let posSeq = 0;
  async function loadPositions(iso: string) {
    const seq = ++posSeq;
    posLoading = true; posError = null;
    try {
      let nextPositions: PositionRow[];
      let nextAccount: number | null = null;
      if (isToday(iso)) {
        const res = await fetch(`/api/hyperliquid/live_positions?wallet=${address}`);
        if (!res.ok) throw new Error(`positions ${res.status}`);
        const body = await res.json();
        nextAccount = body.margin_summary?.account_value ?? null;
        nextPositions = (body.positions ?? []).map(mapLive);
      } else {
        const res = await fetch(`/api/hyperliquid/wallet_positions?wallet=${address}&day=${iso}`);
        if (!res.ok) throw new Error(`positions ${res.status}`);
        const body = await res.json();
        nextPositions = (body.positions ?? []).map(mapHist);
      }
      if (seq !== posSeq) return; // superseded by a newer request — drop this
      positions = nextPositions;
      accountValue = nextAccount;
    } catch (e) {
      if (seq !== posSeq) return;
      posError = (e as Error).message;
      positions = [];
      accountValue = null;
    } finally {
      if (seq === posSeq) posLoading = false;
    }
  }

  // PnL curve loads once (full history; slider doesn't move its right edge).
  $effect(() => {
    address; // re-run if the route address ever changes
    loadPnl();
  });

  // Positions refetch when the snapshot day changes — debounced so dragging
  // the slider doesn't fire a request (incl. the live API) per integer step.
  let posTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const iso = snapshotIso;
    clearTimeout(posTimer);
    posTimer = setTimeout(() => loadPositions(iso), 200);
    return () => clearTimeout(posTimer);
  });
</script>

<div class="px-8 py-6 space-y-6">
  <!-- Header -->
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <div class="flex items-center gap-2">
        <span class="text-zinc-300 text-xs px-2 py-1 rounded-md bg-zinc-900 border border-zinc-700">HL</span>
        <h1 class="text-xl font-semibold font-mono">{truncate(address)}</h1>
        <button
          type="button"
          onclick={copyAddr}
          title="Copy full address"
          class="text-xs text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 rounded border border-zinc-800 hover:border-zinc-600"
        >{copied ? '✓ copied' : 'copy'}</button>
      </div>
      <div class="text-xs text-zinc-500 mt-1 flex items-center gap-3">
        <span class="font-mono break-all">{address}</span>
        <a href={coinglassHlUrl(address)} target="_blank" rel="noopener noreferrer"
           class="underline decoration-dotted hover:text-zinc-200">Coinglass ↗</a>
        <a href={arkhamUrl(address)} target="_blank" rel="noopener noreferrer"
           class="underline decoration-dotted hover:text-zinc-200">Arkham ↗</a>
      </div>
    </div>
  </div>

  <!-- Stat cards (as of the selected day) -->
  <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
    {#snippet card(label: string, value: string, cls = 'text-zinc-200', sub = '')}
      <div class="rounded-lg bg-zinc-900/70 border border-zinc-800 px-3 py-2">
        <div class="text-zinc-500 text-[10px] uppercase tracking-wide">{label}</div>
        <div class="tabular-nums text-sm font-medium {cls}">{value}</div>
        {#if sub}<div class="text-zinc-600 text-[10px]">{sub}</div>{/if}
      </div>
    {/snippet}
    {@render card('Realized PnL', fmtUsd(realizedAsOf), pnlClass(realizedAsOf))}
    {@render card('Unrealized PnL', fmtUsd(unrealAsOf), pnlClass(unrealAsOf))}
    {@render card('Total PnL', fmtUsd(totalAsOf), pnlClass(totalAsOf))}
    {@render card('Open Interest', fmtUsd(oiUsd))}
    {@render card('Positions', String(positions.length))}
    {#if live && accountValue !== null}
      {@render card('Account Value', fmtUsd(accountValue))}
    {:else}
      {@render card('Sharpe', pnlStats ? pnlStats.sharpe.toFixed(2) : '—', 'text-zinc-300', 'window')}
    {/if}
  </div>

  <!-- PnL equity curve -->
  <div class="rounded-lg border border-zinc-800 overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
      <span class="text-zinc-200 font-medium text-sm">PnL</span>
      <span class="text-[10px] text-zinc-600">global · all tokens</span>
      <div class="inline-flex items-center rounded-md border border-zinc-700 overflow-hidden ml-2">
        <button type="button" onclick={() => (pnlMode = 'total')}
          class={'px-2 py-0.5 text-[11px] ' + (pnlMode === 'total' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Realized + unrealized equity curve">Total</button>
        <button type="button" onclick={() => (pnlMode = 'realized')}
          class={'px-2 py-0.5 text-[11px] border-l border-zinc-700 ' + (pnlMode === 'realized' ? 'bg-zinc-800 text-zinc-100' : 'bg-zinc-950 text-zinc-400 hover:text-zinc-200')}
          title="Cumulative realized PnL only">Realized</button>
      </div>
      {#if pnlStats}
        <span class="text-[10px] text-zinc-500 ml-auto">σ {pnlStats.volatility.toFixed(0)} · Sharpe {pnlStats.sharpe.toFixed(2)}</span>
      {/if}
    </div>
    <div class="px-2 py-2">
      {#if pnlError}
        <div class="h-[260px] flex items-center justify-center text-rose-400">{pnlError}</div>
      {:else if pnlLoading && pnlSeries.length === 0}
        <div class="h-[260px] flex items-center justify-center text-zinc-500">loading…</div>
      {:else if pnlSeries.length === 0}
        <div class="h-[260px] flex items-center justify-center text-zinc-500">No PnL history for this wallet.</div>
      {:else}
        <WalletPnlChart
          data={chartData}
          height={260}
          cutoff={selectedUnix}
          label={pnlMode === 'total' ? 'Total' : 'Realized'}
        />
      {/if}
    </div>
  </div>

  <!-- Date slider -->
  <div class="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/30 px-3 py-2 text-xs">
    <span class="text-zinc-500 whitespace-nowrap">As of:</span>
    <span class="font-mono text-zinc-200 whitespace-nowrap">{snapshotIso}{live ? ' (live)' : ''}</span>
    {#if posLoading}
      <span class="inline-block w-3 h-3 rounded-full border-2 border-zinc-600 border-t-blue-400 animate-spin" title="Loading positions…"></span>
    {/if}
    <!-- Slider is locked while positions refetch (the historical query is slow)
         so the view can't get ahead of the data / fire overlapping requests. -->
    <input
      type="range" min="0" max={MAX_BACK} step="1" bind:value={sliderPos}
      disabled={posLoading}
      class="flex-1 accent-blue-500 {posLoading ? 'opacity-50 cursor-wait' : 'cursor-pointer'}"
      title={posLoading ? 'Loading…' : 'Drag to view the wallet as of any past day (1-day grain)'}
    />
    <button type="button" onclick={() => (sliderPos = MAX_BACK)} disabled={posLoading}
      class="text-[10px] text-zinc-500 hover:text-zinc-200 underline decoration-dotted whitespace-nowrap disabled:opacity-40 disabled:hover:text-zinc-500"
      title="Jump to the latest (live) day">Today</button>
  </div>

  <!-- Positions table -->
  <div class="h-[420px]">
    <WalletPositionsTable {positions} {live} loading={posLoading} error={posError} />
  </div>
</div>
