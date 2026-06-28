<script lang="ts">
  // Modal showing the wallets a SmartSelector picked on one day. Triggered
  // by clicking on the wallet-count line on an hl_smart_oi chart. Each
  // wallet row:
  //   - left-click on the address → copy address to clipboard
  //   - middle-click → open https://www.coinglass.com/hyperliquid/<address>
  //   - chevron (right) → expand a collapsible per-wallet PnL view
  //
  // Expandable view (accordion — only one open at a time):
  //   - lazy: the PnL series is fetched only when a row is expanded, and
  //     the cached data is dropped the moment it collapses (or another row
  //     opens), so we never hold stale series for closed rows.
  //   - a total-PnL (unrealized + realized − funding) equity-curve chart
  //     with a daily/weekly timeframe toggle, plus realized / unrealized /
  //     Sharpe / volatility stats.
  //
  // Hide via the ✕, the backdrop, or Escape.
  import WalletPnlChart from '$lib/components/WalletPnlChart.svelte';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { fmtUsdTooltip, fmtAmountTooltip } from '$lib/components/charts/config';
  import { metricDef } from '$lib/components/charts/smartSelector';

  /** One selector metric surfaced "as of" the clicked day. */
  type AsOfMetric = { key: string; label: string; scope: string; lookback: number };

  type Props = {
    open: boolean;
    /** Loaded list of wallet addresses (may be empty during fetch). */
    wallets: string[];
    /** Selector metrics (sort + criteria) shown as-of the clicked day, in
     *  rank/sort order. Empty for a pure-composite filter (no own criteria). */
    asOfMetrics?: AsOfMetric[];
    /** address → { metricKey → value } as the selector computed it on `day`. */
    walletMetrics?: Record<string, Record<string, number | null>>;
    /** address → the wallet's chart-token position at `day` (long/short side,
     *  token amount, USD notional, unrealized PnL). Absent = no position. */
    walletPositions?: Record<string, { side: string; amount: number; size_usd: number; unrealized: number }>;
    /** Loading state — true while the smart_wallets fetch is in flight. */
    loading?: boolean;
    /** Error message if the fetch failed. */
    error?: string | null;
    /** ISO date the wallets are for (display label + PnL window end). */
    day?: string;
    /** Token context (shown for clarity). */
    token?: string;
    /** Close handler. */
    onClose: () => void;
  };

  let {
    open,
    wallets,
    asOfMetrics = [],
    walletMetrics = {},
    walletPositions = {},
    loading = false,
    error: errMsg = null,
    day = '',
    token = '',
    onClose
  }: Props = $props();

  // Format an as-of metric value by its catalogue kind. The value is exactly
  // what the selector computed at the admission day (no unit re-scaling).
  function fmtMetric(key: string, v: number | null | undefined): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    const kind = metricDef(key)?.kind;
    if (kind === 'usd') return fmtUsdTooltip(v);
    if (kind === 'token') return fmtAmountTooltip(v);
    // ratio / count / pct → plain number; ratios & counts read best at 2dp.
    return Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2);
  }

  let toast = $state<{ text: string; at: number } | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function flashToast(text: string) {
    toast = { text, at: Date.now() };
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast = null; }, 1200);
  }

  async function copyAddress(w: string) {
    try {
      await navigator.clipboard.writeText(w);
      flashToast(`Copied ${w.slice(0, 6)}…${w.slice(-4)}`);
    } catch {
      flashToast('Copy failed');
    }
  }

  // Middle-click / Ctrl-click opens the internal HL wallet page (as of the
  // dialog's snapshot day when present), not Coinglass.
  function walletUrl(w: string): string {
    return `/wallet/hl/${w}` + (day ? `?snapshot=${day}` : '');
  }

  // Collapsed-view position label before each address: the signed notional of
  // the wallet's chart-token position at the filter day — +<notional> green for
  // long, −<notional> red for short, gray "N/A" when there's no position.
  function posText(w: string): string {
    const p = walletPositions[w];
    if (!p) return 'N/A';
    return (p.side === 'long' ? '+' : '-') + fmtUsdTooltip(p.size_usd);
  }
  function posClass(w: string): string {
    const side = walletPositions[w]?.side;
    if (side === 'long') return 'text-emerald-400';
    if (side === 'short') return 'text-red-400';
    return 'text-zinc-600';
  }
  function posTitle(w: string): string {
    const p = walletPositions[w];
    const tk = token || 'token';
    if (!p) return `No ${tk} position on ${day}`;
    return `${p.side === 'long' ? 'Long' : 'Short'} ${tk} · ${fmtUsdTooltip(p.size_usd)} notional · ${fmtUsdTooltip(p.unrealized)} unrealized (as of ${day})`;
  }

  // The "as-of" cutoff day (header day) as Unix seconds at UTC midnight —
  // drawn as a dashed vertical marker on the PnL chart. null when no day.
  let cutoff = $derived.by<number | null>(() => {
    if (!day) return null;
    const t = Date.parse(`${day}T00:00:00Z`);
    return Number.isFinite(t) ? Math.floor(t / 1000) : null;
  });

  // When a Sharpe metric is in the filter, mark the start of its lookback
  // window (cutoff − lookback days) so the chart shows exactly the span the
  // (annualized) Sharpe was computed over, relative to the filter day.
  let sharpeLookback = $derived(
    asOfMetrics.find((m) => m.key === 'sharpe' || m.key === 'sharpe_realized')?.lookback ?? null
  );
  let lookbackStart = $derived(
    cutoff != null && sharpeLookback != null ? cutoff - sharpeLookback * 86_400 : null
  );

  // ── Expandable per-wallet PnL view (accordion, lazy, drop-on-collapse) ──
  type Point = { time: number; value: number };
  type PnlStats = {
    realized_pnl: number;
    unrealized_pnl: number;
    sharpe: number;
    volatility: number;
  };
  type RawPoint = { time: number; realized: number; total: number };

  let expanded = $state<string | null>(null);     // the single open wallet
  let tf = $state<'daily' | 'weekly'>('daily');
  // Which cumulative curve to plot: realized only, or realized + EOD
  // unrealized snapshot ("total").
  let pnlMode = $state<'realized' | 'total'>('realized');
  // Curve scope: global (all tokens) or this chart's token. Only offered when
  // there's a token; defaults to the criterion's scope so a token-scoped
  // Sharpe filter opens on the matching token curve.
  let pnlScope = $state<'global' | 'token'>('global');
  // The filter's criteria are token-scoped (so default the curve to token).
  let criterionIsTokenScoped = $derived(
    !!token && asOfMetrics.some((m) => m.scope === 'token')
  );
  let pnlLoading = $state(false);
  let pnlError = $state<string | null>(null);
  let pnlSeries = $state<RawPoint[]>([]);          // full daily series
  let pnlStats = $state<PnlStats | null>(null);
  let pnlCtl: AbortController | null = null;

  // Close-price overlay (chart token). Off by default; per-token (same for all
  // wallets), so fetched once and cached for the dialog's lifetime.
  let showClose = $state(false);
  let closeRaw = $state<Point[]>([]);              // daily close series
  let closeCtl: AbortController | null = null;

  async function loadClose() {
    if (!token || closeRaw.length) return;
    closeCtl = new AbortController();
    try {
      const res = await fetch(`/api/hyperliquid/token_close?token=${encodeURIComponent(token)}`, { signal: closeCtl.signal });
      if (!res.ok) throw new Error(`token_close ${res.status}`);
      const body = await res.json();
      closeRaw = ((body.series ?? []) as Array<{ time: number; close: number }>).map((r) => ({ time: r.time, value: r.close }));
    } catch (e) {
      if ((e as DOMException)?.name !== 'AbortError') showClose = false;
    }
  }
  function toggleClose() {
    showClose = !showClose;
    if (showClose) void loadClose();
  }

  function clearPnl() {
    if (pnlCtl) { pnlCtl.abort(); pnlCtl = null; }
    pnlSeries = [];
    pnlStats = null;
    pnlError = null;
    pnlLoading = false;
  }

  function toggleExpand(w: string) {
    if (expanded === w) {
      // Collapse → drop cached data for this wallet.
      expanded = null;
      clearPnl();
      return;
    }
    // Opening a (different) wallet collapses any other and drops its data.
    expanded = w;
    tf = 'daily';
    // Default the curve scope to match the filter's criterion scope.
    pnlScope = criterionIsTokenScoped ? 'token' : 'global';
    clearPnl();
    void loadPnl(w);
  }

  // User flips the Global/Token toggle while a row is open → re-fetch.
  function setPnlScope(s: 'global' | 'token') {
    if (pnlScope === s) return;
    pnlScope = s;
    if (expanded) { clearPnl(); void loadPnl(expanded); }
  }

  async function loadPnl(w: string) {
    pnlLoading = true;
    pnlError = null;
    pnlCtl = new AbortController();
    try {
      const qs = new URLSearchParams({ wallet: w });
      // Token-scope the curve to match a token-scoped criterion; else global.
      if (pnlScope === 'token' && token) qs.set('token', token);
      // Intentionally NOT pinned to the clicked day — the PnL view shows the
      // wallet's full recent history (server defaults to a 180-day window
      // ending today), not just up to the bucket that opened the dialog.
      const res = await fetch(`/api/hyperliquid/wallet_pnl?${qs}`, { signal: pnlCtl.signal });
      if (!res.ok) throw new Error(`wallet_pnl ${res.status}`);
      const body = await res.json();
      // Guard against a late response after the row was collapsed / swapped.
      if (expanded !== w) return;
      pnlSeries = ((body.series ?? []) as Array<{ time: number; realized: number; total: number }>).map((r) => ({
        time: r.time,
        realized: r.realized,
        total: r.total
      }));
      pnlStats = (body.stats ?? null) as PnlStats | null;
    } catch (e) {
      if ((e as DOMException)?.name !== 'AbortError') {
        pnlError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      if (expanded === w) pnlLoading = false;
    }
  }

  // Daily → weekly downsample of the cumulative equity curve: take the last
  // point in each 7-day (UTC) bucket. Sampling the cumulative total at week
  // end is the correct weekly view of an equity curve.
  let chartData = $derived.by<Point[]>(() => {
    const src = pnlSeries;
    if (tf === 'daily') return src.map((d) => ({ time: d.time, value: d[pnlMode] }));
    const WEEK = 7 * 86_400;
    const lastPerWeek = new Map<number, RawPoint>();
    for (const d of src) lastPerWeek.set(Math.floor(d.time / WEEK), d);
    return [...lastPerWeek.values()]
      .sort((a, b) => a.time - b.time)
      .map((d) => ({ time: d.time, value: d[pnlMode] }));
  });

  // Close overlay downsampled to the same timeframe as the PnL curve (last
  // close of each 7-day bucket for weekly), so the two series stay aligned.
  let closeChartData = $derived.by<Point[]>(() => {
    if (!showClose) return [];
    if (tf === 'daily') return closeRaw;
    const WEEK = 7 * 86_400;
    const lastPerWeek = new Map<number, Point>();
    for (const d of closeRaw) lastPerWeek.set(Math.floor(d.time / WEEK), d);
    return [...lastPerWeek.values()].sort((a, b) => a.time - b.time);
  });

  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') onClose();
  }

  // Reset all expansion state whenever the dialog is closed or its wallet
  // list changes (a new day was clicked). Also drop the close overlay cache so
  // a new token re-fetches.
  $effect(() => {
    void wallets;
    if (!open) {
      expanded = null;
      clearPnl();
      showClose = false;
      if (closeCtl) { closeCtl.abort(); closeCtl = null; }
      closeRaw = [];
    }
  });
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm cursor-default"
    role="dialog"
    aria-modal="true"
    onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
    tabindex="-1"
    use:stopDragEvents
  >
    <div class="w-[56rem] max-w-[95vw] max-h-[90vh] bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl flex flex-col text-base">
      <header class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
        <div class="flex items-center gap-2">
          <span class="text-zinc-300 font-medium">Smart wallets</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-400">{token}</span>{/if}
          {#if day}<span class="text-zinc-500">·</span><span class="text-zinc-400">{day}</span>{/if}
        </div>
        <button
          type="button"
          class="text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 cursor-pointer"
          onclick={onClose}
          aria-label="Close"
        >✕</button>
      </header>

      <div class="px-4 py-2 text-sm text-zinc-500 border-b border-zinc-800">
        <span class="text-zinc-400">Click</span> to copy address ·
        <span class="text-zinc-400">middle-click</span> (or Ctrl-click)
        to open the wallet page · <span class="text-zinc-400">chevron</span> for PnL.
      </div>

      <div class="flex-1 overflow-auto scrollbar-none">
        {#if loading}
          <div class="px-4 py-6 text-zinc-400 text-center">Loading wallets…</div>
        {:else if errMsg}
          <div class="px-4 py-6 text-red-400 text-center">{errMsg}</div>
        {:else if wallets.length === 0}
          <div class="px-4 py-6 text-zinc-500 text-center">No wallets passed the criteria on this day.</div>
        {:else}
          <table class="w-full text-sm font-mono">
            <thead class="text-zinc-500 text-[11px] uppercase tracking-widest">
              <tr class="border-b border-zinc-800">
                <th class="px-4 py-1.5 text-left">#</th>
                <th class="px-4 py-1.5 text-left">Address</th>
                <th class="px-2 py-1.5 text-right"></th>
                <th class="pl-2 pr-5 py-1.5 text-right"></th>
              </tr>
            </thead>
            <tbody>
              {#each wallets as w, i (w)}
                <tr class="border-b border-zinc-800 hover:bg-zinc-900/60">
                  <td class="px-4 py-1.5 text-zinc-500 tabular-nums w-12">{i + 1}</td>
                  <td class="px-4 py-1.5">
                    <!-- Signed notional of the chart-token position at the
                         filter day (+green long / −red short / gray N/A). -->
                    <span
                      class="font-mono tabular-nums text-sm font-semibold mr-2 {posClass(w)}"
                      title={posTitle(w)}
                    >{posText(w)}</span>
                    <!-- Anchor so middle-click + Ctrl-click open the wallet
                         page via the browser's default new-tab behaviour.
                         Left-click is intercepted and copies. -->
                    <a
                      href={walletUrl(w)}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-zinc-100 hover:text-emerald-300 break-all cursor-pointer"
                      onclick={(e) => { e.preventDefault(); copyAddress(w); }}
                      title="Click to copy · middle-click / Ctrl-click to open the wallet page"
                    >{w}</a>
                  </td>
                  <td class="px-2 py-1.5 text-right">
                    <a
                      href={walletUrl(w)}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-zinc-500 hover:text-emerald-300 cursor-pointer"
                      title="Open wallet page in new tab"
                    >↗</a>
                  </td>
                  <td class="pl-2 pr-5 py-1.5 text-right">
                    <button
                      type="button"
                      class="text-zinc-500 hover:text-zinc-200 transition-transform inline-block cursor-pointer"
                      class:rotate-90={expanded === w}
                      onclick={() => toggleExpand(w)}
                      aria-label={expanded === w ? 'Collapse PnL' : 'Show PnL'}
                      aria-expanded={expanded === w}
                    >›</button>
                  </td>
                </tr>
                {#if expanded === w}
                  <tr class="border-b border-zinc-800 bg-zinc-900/40">
                    <td colspan="4" class="px-4 py-3">
                      {#if asOfMetrics.length > 0 && walletMetrics[w]}
                        <!-- The metric values the SELECTOR computed at the
                             clicked day — i.e. what admitted this wallet to the
                             filtered set (not a current-time recomputation). -->
                        <div class="mb-3">
                          <div class="text-[11px] uppercase tracking-wide text-zinc-500 mb-1.5">
                            As of {day} · filter values
                          </div>
                          <div class="grid grid-cols-3 gap-2 text-sm">
                            {#each asOfMetrics as m (m.key)}
                              <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                                <div class="text-zinc-500 text-[11px] uppercase tracking-wide truncate" title={m.label}>
                                  {m.label}
                                </div>
                                <div class="text-zinc-200 tabular-nums">{fmtMetric(m.key, walletMetrics[w]?.[m.key])}</div>
                                <div class="text-zinc-600 text-[10px]">{m.scope === 'token' ? token || 'token' : 'global'} · {m.lookback}d</div>
                              </div>
                            {/each}
                          </div>
                        </div>
                      {/if}
                      {#if token}
                        <!-- The wallet's position in the chart token AS OF the
                             filter day (the current position at filter time). -->
                        <div class="mb-3">
                          <div class="text-[11px] uppercase tracking-wide text-zinc-500 mb-1.5">
                            Position at {day} · {token}
                          </div>
                          {#if walletPositions[w]}
                            {@const p = walletPositions[w]}
                            <div class="grid grid-cols-4 gap-2 text-sm">
                              <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                                <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Side</div>
                                <div class={p.side === 'long' ? 'text-emerald-300' : 'text-red-300'}>
                                  {p.side === 'long' ? 'Long' : 'Short'}
                                </div>
                              </div>
                              <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                                <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Notional</div>
                                <div class="text-zinc-200">{fmtUsdTooltip(p.size_usd)}</div>
                              </div>
                              <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                                <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Size</div>
                                <div class="text-zinc-200">{fmtAmountTooltip(p.amount)} {token}</div>
                              </div>
                              <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                                <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Unrealized</div>
                                <div class={p.unrealized >= 0 ? 'text-emerald-300' : 'text-red-300'}>
                                  {fmtUsdTooltip(p.unrealized)}
                                </div>
                              </div>
                            </div>
                          {:else}
                            <div class="text-zinc-500 text-sm">No open {token} position on {day}.</div>
                          {/if}
                        </div>
                      {/if}
                      {#if pnlLoading}
                        <div class="py-6 text-center text-zinc-400 text-xs">Loading PnL…</div>
                      {:else if pnlError}
                        <div class="py-6 text-center text-red-400 text-xs">{pnlError}</div>
                      {:else}
                        <div class="flex items-center justify-between mb-2">
                          <div class="flex items-center gap-2">
                            <div class="flex gap-1">
                              {#each ['realized', 'total'] as const as m}
                                <button
                                  type="button"
                                  class="px-2.5 py-1 rounded text-xs border cursor-pointer capitalize"
                                  class:border-emerald-600={pnlMode === m}
                                  class:text-emerald-300={pnlMode === m}
                                  class:border-zinc-700={pnlMode !== m}
                                  class:text-zinc-400={pnlMode !== m}
                                  onclick={() => (pnlMode = m)}
                                >{m}</button>
                              {/each}
                            </div>
                            {#if token}
                              <div class="flex gap-1" title="Scope the PnL curve: all the wallet's tokens, or just this chart's token">
                                {#each [['global', 'Global'], ['token', token]] as const as [s, lbl]}
                                  <button
                                    type="button"
                                    class="px-2.5 py-1 rounded text-xs border cursor-pointer"
                                    class:border-sky-600={pnlScope === s}
                                    class:text-sky-300={pnlScope === s}
                                    class:border-zinc-700={pnlScope !== s}
                                    class:text-zinc-400={pnlScope !== s}
                                    onclick={() => setPnlScope(s)}
                                  >{lbl}</button>
                                {/each}
                              </div>
                            {/if}
                            <span class="text-[11px] text-zinc-500">
                              cumulative, {pnlScope === 'token' && token ? token : 'all tokens'}{pnlMode === 'total' ? ' · + EOD unrealized' : ''}
                            </span>
                          </div>
                          <div class="flex items-center gap-1">
                            {#if token}
                              <button
                                type="button"
                                class="px-2.5 py-1 rounded text-xs border cursor-pointer mr-1"
                                class:border-blue-500={showClose}
                                class:text-blue-300={showClose}
                                class:border-zinc-700={!showClose}
                                class:text-zinc-400={!showClose}
                                onclick={toggleClose}
                                title="Overlay {token} close price"
                              >{token} close</button>
                            {/if}
                            {#each ['daily', 'weekly'] as const as t}
                              <button
                                type="button"
                                class="px-2.5 py-1 rounded text-xs border cursor-pointer"
                                class:border-emerald-600={tf === t}
                                class:text-emerald-300={tf === t}
                                class:border-zinc-700={tf !== t}
                                class:text-zinc-400={tf !== t}
                                onclick={() => (tf = t)}
                              >{t}</button>
                            {/each}
                          </div>
                        </div>
                        {#if chartData.length === 0}
                          <div class="py-6 text-center text-zinc-500 text-xs">No PnL history in range.</div>
                        {:else}
                          <WalletPnlChart data={chartData} closeData={closeChartData} height={200} {cutoff} {lookbackStart} label={pnlMode === 'total' ? 'Total' : 'Realized'} />
                          <div class="flex items-center gap-3 mt-1 text-[11px] text-zinc-500">
                            <span class="flex items-center gap-1"><span class="inline-block w-3 border-t border-dashed" style="border-color:#fbbf24"></span>filter day{day ? ` (${day})` : ''}</span>
                            {#if lookbackStart != null}
                              <span class="flex items-center gap-1"><span class="inline-block w-3 border-t border-dashed" style="border-color:#38bdf8"></span>Sharpe lookback start (−{sharpeLookback}d)</span>
                            {/if}
                            {#if showClose && closeChartData.length > 0}
                              <span class="flex items-center gap-1"><span class="inline-block w-3 border-t" style="border-color:#3b82f6"></span>{token} close (left axis)</span>
                            {/if}
                          </div>
                        {/if}
                        {#if pnlStats && asOfMetrics.length === 0}
                          <!-- Fallback (pure-composite filter has no own
                               metrics to surface): current-time PnL stats. -->
                          <div class="grid grid-cols-4 gap-2 mt-3 text-sm">
                            <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                              <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Realized</div>
                              <div class={pnlStats.realized_pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}>
                                {fmtUsdTooltip(pnlStats.realized_pnl)}
                              </div>
                            </div>
                            <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                              <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Unrealized</div>
                              <div class={pnlStats.unrealized_pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}>
                                {fmtUsdTooltip(pnlStats.unrealized_pnl)}
                              </div>
                            </div>
                            <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                              <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Sharpe (ann.)</div>
                              <div class="text-zinc-200">{pnlStats.sharpe.toFixed(2)}</div>
                            </div>
                            <div class="rounded bg-zinc-900/70 px-2 py-1.5">
                              <div class="text-zinc-500 text-[11px] uppercase tracking-wide">Volatility</div>
                              <div class="text-zinc-200">{fmtAmountTooltip(pnlStats.volatility)}</div>
                            </div>
                          </div>
                        {/if}
                      {/if}
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      {#if wallets.length > 0 && !loading}
        <footer class="px-4 py-1.5 border-t border-zinc-800 text-sm text-zinc-500 text-right">
          {wallets.length} wallet{wallets.length === 1 ? '' : 's'}
        </footer>
      {/if}
    </div>
  </div>

  {#if toast}
    <div class="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] bg-zinc-900 border border-zinc-700 rounded-md px-3 py-1.5 text-xs text-zinc-200 shadow-lg pointer-events-none">
      {toast.text}
    </div>
  {/if}
{/if}
