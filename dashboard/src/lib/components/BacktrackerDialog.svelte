<script lang="ts">
  // Backtracker dialog: the wallets whose OPEN position in one token changed most
  // over a lookback ending at the clicked bar. Rows come pre-ranked by |Δ| from
  // /api/hyperliquid/position_change_wallets (signed amounts: long +, short −).
  // Columns: Address (copy / middle-click → wallet page), Change type, Change
  // amount (token + $, with % for inc/dec), New position (token + $), Old
  // unrealized PnL. Headers are client-sortable. Close via ✕ / backdrop / Esc.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';
  import { tzShortLabel } from '$lib/stores/timezone.svelte';

  type Row = {
    wallet: string;
    amt_old: number; amt_new: number;
    usd_old: number; usd_new: number;
    unrealized_old: number;
    account_value?: number;
    gross_buy?: number; gross_sell?: number;
    realized_pnl?: number;
    categories?: string[];
  };

  let {
    open,
    rows = [] as Row[],
    price = 0,
    token = '',
    lookback = '',
    timeLabel = '',
    startLabel = '',
    endLabel = '',
    snapshotDate = '',
    loading = false,
    error: errMsg = null,
    groupName = null,
    groupOnly = false,
    onToggleGroupOnly = undefined,
    startSec = 0,
    endSec = 0,
    groupId = null,
    onClose
  }: {
    open: boolean;
    rows?: Row[];
    price?: number;
    token?: string;
    lookback?: string;
    timeLabel?: string;
    /** The two 15-min snapshot boundaries compared (start = T−lookback, end = T). */
    startLabel?: string;
    endLabel?: string;
    snapshotDate?: string;
    loading?: boolean;
    error?: string | null;
    /** When the backtracker has a wallet group selected, its name — enables the
     *  "Only <group>" filter toggle. null = no group → toggle hidden. */
    groupName?: string | null;
    groupOnly?: boolean;
    onToggleGroupOnly?: (() => void) | undefined;
    /** Raw window (unix secs) the server compared — drives the Aggregate-mode fetch. */
    startSec?: number;
    endSec?: number;
    /** Wallet-group id for the Aggregate fetch when "Only <group>" is on. */
    groupId?: string | null;
    onClose: () => void;
  } = $props();

  // ── Aggregate mode: per-wallet classified breakdown (opened/increased/decreased/
  // closed/flipped × long/short) — the same categories as Trading Pit "Overview",
  // rendered as one card per wallet. Fetched lazily from /trading_pit (mode=
  // overview_wallets) over this dialog's exact window, only while toggled on. ──
  type OverviewRow = { wallet: string; cats?: string[] } & Record<string, [number, number] | number | string | string[] | undefined>;
  let aggregateMode = $state(false);
  let aggWallets = $state<OverviewRow[]>([]);
  let aggFlipSplit = $state(false);
  let aggLoading = $state(false);
  let aggError = $state<string | null>(null);
  let aggCtl: AbortController | null = null;

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
  const ovCols = $derived(aggFlipSplit ? OV_BASE : [...OV_BASE, 'flip_ls', 'flip_sl']);
  const cellOf = (r: OverviewRow, k: string): [number, number] => {
    const v = r[k];
    return Array.isArray(v) && typeof v[0] === 'number' ? (v as [number, number]) : [0, 0];
  };
  // Net position change: increased longs + decreased shorts − increased shorts − decreased
  // longs; parenthesis = the number of fills making it up (this is a single wallet).
  const netValue = (r: OverviewRow) =>
    cellOf(r, 'inc_long')[0] + cellOf(r, 'dec_short')[0] - cellOf(r, 'inc_short')[0] - cellOf(r, 'dec_long')[0];
  const netCount = (r: OverviewRow) =>
    cellOf(r, 'inc_long')[1] + cellOf(r, 'dec_short')[1] + cellOf(r, 'inc_short')[1] + cellOf(r, 'dec_long')[1];
  const shownCount = $derived(aggregateMode ? aggWallets.length : rows.length);

  async function loadAgg(tok: string, s: number, e: number, go: boolean, gid: string | null) {
    if (!tok || !e) { aggWallets = []; return; }
    aggCtl?.abort();
    const c = new AbortController();
    aggCtl = c;
    aggLoading = true;
    aggError = null;
    try {
      const qs = new URLSearchParams({
        tokens: tok, mode: 'overview_wallets',
        since: new Date(s * 1000).toISOString(), until: new Date(e * 1000).toISOString(), n: '100'
      });
      if (go && gid) qs.set('group', gid);
      const res = await fetch(`/api/hyperliquid/trading_pit?${qs}`, { signal: c.signal });
      if (!res.ok) throw new Error(`aggregate ${res.status}`);
      const body = await res.json();
      aggWallets = (body.wallets ?? []) as OverviewRow[];
      aggFlipSplit = !!body.flip_split;
    } catch (err) {
      if ((err as Error).name !== 'AbortError') { aggError = (err as Error).message; aggWallets = []; }
    } finally {
      if (aggCtl === c) aggLoading = false;
    }
  }
  // Fetch (and re-fetch) whenever aggregate mode is on and the window/token/group change.
  $effect(() => {
    const on = aggregateMode, o = open, tok = token, s = startSec, e = endSec, go = groupOnly, gid = groupId;
    if (o && on) void loadAgg(tok, s, e, go, gid);
  });

  function fmtUsd(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtAmt(n: number): string {
    if (!isFinite(n)) return '—';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(1) + 'K';
    return sign + abs.toFixed(2);
  }

  const EPS = 1e-9;
  // dAmt = signed position change; > 0 = net bought/covered (more long / less
  // short), < 0 = net sold/shorted.
  function dAmt(r: Row): number { return r.amt_new - r.amt_old; }
  function changeType(r: Row): string {
    const o = r.amt_old, n = r.amt_new;
    if (Math.abs(o) < EPS) return n > 0 ? 'Open long' : 'Open short';
    if (Math.abs(n) < EPS) return o > 0 ? 'Close long' : 'Close short';
    if (o > 0 && n > 0) return Math.abs(n) > Math.abs(o) ? 'Increase long' : 'Decrease long';
    if (o < 0 && n < 0) return Math.abs(n) > Math.abs(o) ? 'Increase short' : 'Decrease short';
    return o > 0 && n < 0 ? 'Flip → short' : 'Flip → long';
  }
  // % change vs the old position size — only meaningful for increase/decrease
  // (same side, both non-zero). Signed: + for increase, − for decrease.
  function changePct(r: Row): number | null {
    const o = r.amt_old, n = r.amt_new;
    if (Math.abs(o) < EPS || Math.abs(n) < EPS) return null;
    if ((o > 0) !== (n > 0)) return null; // flip → no meaningful %
    return ((Math.abs(n) - Math.abs(o)) / Math.abs(o)) * 100;
  }
  function fmtPct(n: number | null): string {
    if (n === null || !isFinite(n)) return '';
    return ` (${n > 0 ? '+' : ''}${n.toFixed(0)}%)`;
  }
  function sideOf(v: number): 'long' | 'short' | 'flat' {
    return v > EPS ? 'long' : v < -EPS ? 'short' : 'flat';
  }

  // ── client-side sort. '' = server order (already |Δ| desc). ──
  let sortKey = $state<string>('');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: string) {
    if (sortKey === k) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: string): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  function sortVal(r: Row, k: string): number {
    if (k === 'change') return Math.abs(dAmt(r));
    if (k === 'new') return Math.abs(r.usd_new);
    if (k === 'upnl') return r.unrealized_old;
    if (k === 'acct') return r.account_value ?? 0;
    if (k === 'gross') return (r.gross_buy ?? 0) + (r.gross_sell ?? 0);
    if (k === 'pnl') return r.realized_pnl ?? 0;
    return 0;
  }
  let sortedRows = $derived.by(() => {
    if (!sortKey) return rows;
    const dir = sortDir;
    return [...rows].sort((a, b) => (sortVal(a, sortKey) - sortVal(b, sortKey)) * dir);
  });

  // Address cell (copy + middle-click → wallet page, and the group/tag capsules)
  // is handled by the shared WalletAddress component.
  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') onClose();
  }
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm cursor-default"
    role="dialog" aria-modal="true" tabindex="-1"
    onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
    use:stopDragEvents
  >
    <div class="w-[92rem] max-w-[97vw] max-h-[90vh] bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl flex flex-col text-sm">
      <header class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
        <div class="flex items-center gap-2">
          <span class="text-zinc-300 font-medium">Position changes</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-200">{token}</span>{/if}
          {#if lookback === 'none'}
            <span class="text-zinc-500">· current bar</span>
          {:else}
            {#if lookback}<span class="text-zinc-500">· Δ</span><span class="text-zinc-400">{lookback}</span>{/if}
            {#if timeLabel}<span class="text-zinc-500">to</span><span class="text-zinc-400">{timeLabel} {tzShortLabel()}</span>{/if}
          {/if}
        </div>
        <div class="flex items-center gap-2">
          <!-- Aggregate Mode: swap the per-wallet table for per-wallet cards showing the
               classified breakdown (opened/increased/decreased/closed/flipped). -->
          <button
            type="button"
            onclick={() => (aggregateMode = !aggregateMode)}
            class="text-xs px-2 py-0.5 rounded border transition-colors {aggregateMode
              ? 'bg-blue-600 border-blue-500 text-white'
              : 'border-zinc-700 text-zinc-400 hover:text-zinc-200'}"
            title="Show each wallet's classified position changes (opened / increased / decreased / closed / flipped × long / short) as cards"
          >Aggregate Mode</button>
          {#if groupName && onToggleGroupOnly}
            <!-- Filter to the backtracker's selected wallet group (vs all wallets). -->
            <button
              type="button"
              onclick={onToggleGroupOnly}
              class="text-xs px-2 py-0.5 rounded border transition-colors {groupOnly
                ? 'bg-blue-600 border-blue-500 text-white'
                : 'border-zinc-700 text-zinc-400 hover:text-zinc-200'}"
              title="Show only wallets in the '{groupName}' group (the backtracker's selected group)"
            >Only {groupName}</button>
          {/if}
          <button type="button" class="text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 cursor-pointer" onclick={onClose} aria-label="Close">✕</button>
        </div>
      </header>
      <div class="px-4 py-2 text-xs text-zinc-500 border-b border-zinc-800">
        <span class="text-zinc-400">Click</span> address to copy ·
        <span class="text-zinc-400">middle-click</span> (or Ctrl-click) to open the wallet page.
        Ranked by |position change|.
      </div>

      <div class="flex-1 overflow-auto scrollbar-none">
        {#if aggregateMode}
          {#if aggLoading}
            <div class="px-4 py-6 text-zinc-400 text-center">Loading…</div>
          {:else if aggError}
            <div class="px-4 py-6 text-red-400 text-center">{aggError}</div>
          {:else if aggWallets.length === 0}
            <div class="px-4 py-6 text-zinc-500 text-center">No position changes in this window.</div>
          {:else}
            <!-- one card per wallet: its classified breakdown, same categories as Overview -->
            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-3">
              {#each aggWallets as r (r.wallet)}
                <div class="border border-zinc-800 rounded-lg bg-zinc-900/30 p-3 flex flex-col gap-2">
                  <WalletAddress address={r.wallet} auxKind="wallet" snapshot={snapshotDate} tags={r.cats ?? []} token={token} />
                  <div class="flex items-center justify-between text-xs border-b border-zinc-800 pb-2">
                    <span class="text-zinc-500">Net Pos Change</span>
                    <span class="font-mono tabular-nums {netValue(r) > 0 ? 'text-emerald-400' : netValue(r) < 0 ? 'text-rose-400' : 'text-zinc-600'}">
                      {netCount(r) ? `${fmtUsd(netValue(r))} (${netCount(r)})` : '—'}
                    </span>
                  </div>
                  <div class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    {#each ovCols as c (c)}
                      {@const cell = cellOf(r, c)}
                      <div class="flex items-center justify-between gap-2">
                        <span class="{typeCls(c)} truncate">{typeLabel(c)}</span>
                        <span class="font-mono tabular-nums whitespace-nowrap {cell[1] ? 'text-zinc-200' : 'text-zinc-700'}">
                          {cell[1] ? `${fmtUsd(cell[0])} (${cell[1]})` : '—'}
                        </span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        {:else if loading}
          <div class="px-4 py-6 text-zinc-400 text-center">Loading…</div>
        {:else if errMsg}
          <div class="px-4 py-6 text-red-400 text-center">{errMsg}</div>
        {:else if rows.length === 0}
          <div class="px-4 py-6 text-zinc-500 text-center">No position changes in this window.</div>
        {:else}
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-zinc-950 text-zinc-500 text-[11px] uppercase tracking-wide border-b border-zinc-800">
              <tr>
                <th class="px-3 py-1.5 text-left font-normal">#</th>
                <th class="px-3 py-1.5 text-left font-normal">Address</th>
                <th class="px-3 py-1.5 text-left font-normal">Change type</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('change')}>Change{sortArrow('change')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('new')}>New position{sortArrow('new')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('upnl')}>Old uPnL{sortArrow('upnl')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('acct')} title="Total open interest — the wallet's total open position value (notional) across all tokens at the snapshot">Total OI{sortArrow('acct')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('gross')} title="Gross buy / sell fill volume ($, execution price) in this token over the window — both legs of round-trips, so it reconciles with the chart's flow marker (unlike the net Change column)">Bought / Sold{sortArrow('gross')}</th>
                <th class="px-3 py-1.5 text-right font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('pnl')} title="Realized PnL — Σ closing-fill closed_pnl in this token over the window. 0 when only opening/increasing; non-zero on decreases/closes.">Realized PnL{sortArrow('pnl')}</th>
                <th class="px-3 py-1.5 text-left font-normal" title="The two 15-min snapshots compared: start (T−lookback) → end (clicked bar)">Snapshots (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {#each sortedRows as r, i (r.wallet)}
                <tr class="border-b border-zinc-900 hover:bg-zinc-900/50">
                  <td class="px-3 py-1.5 text-zinc-500 tabular-nums">{i + 1}</td>
                  <td class="px-3 py-1.5">
                    <WalletAddress address={r.wallet} auxKind="wallet" snapshot={snapshotDate} tags={r.categories ?? []} />
                  </td>
                  <td class="px-3 py-1.5">
                    <span class={dAmt(r) > 0 ? 'text-emerald-400' : 'text-rose-400'}>{changeType(r)}</span>
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {dAmt(r) > 0 ? 'text-emerald-400' : 'text-rose-400'}">
                    {dAmt(r) > 0 ? '+' : '−'}{fmtUsd(Math.abs(dAmt(r)) * price)}{fmtPct(changePct(r))}
                    <div class="text-[11px] text-zinc-500">{fmtAmt(Math.abs(dAmt(r)))} {token}</div>
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums">
                    {#if sideOf(r.amt_new) === 'flat'}
                      <span class="text-zinc-500">—</span>
                    {:else}
                      <span class={sideOf(r.amt_new) === 'long' ? 'text-emerald-300' : 'text-rose-300'}>
                        {sideOf(r.amt_new) === 'long' ? 'Long' : 'Short'} {fmtUsd(Math.abs(r.usd_new))}
                      </span>
                      <div class="text-[11px] text-zinc-500">{fmtAmt(Math.abs(r.amt_new))} {token}</div>
                    {/if}
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {r.unrealized_old >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
                    {fmtUsd(r.unrealized_old)}
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums text-zinc-300">
                    {r.account_value ? fmtUsd(r.account_value) : '—'}
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums whitespace-nowrap">
                    {#if (r.gross_buy ?? 0) === 0 && (r.gross_sell ?? 0) === 0}
                      <span class="text-zinc-600">—</span>
                    {:else}
                      <span class="text-emerald-400">{fmtUsd(r.gross_buy ?? 0)}</span><span class="text-zinc-600">/</span><span class="text-rose-400">{fmtUsd(r.gross_sell ?? 0)}</span>
                    {/if}
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono tabular-nums {(r.realized_pnl ?? 0) > 0 ? 'text-emerald-400' : (r.realized_pnl ?? 0) < 0 ? 'text-rose-400' : 'text-zinc-500'}">
                    {(r.realized_pnl ?? 0) === 0 ? '—' : fmtUsd(r.realized_pnl ?? 0)}
                  </td>
                  <td class="px-3 py-1.5 font-mono text-[11px] text-zinc-400 whitespace-nowrap">
                    {startLabel}<span class="text-zinc-600"> → </span>{endLabel}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      {#if shownCount > 0 && !(aggregateMode ? aggLoading : loading)}
        <footer class="px-4 py-1.5 border-t border-zinc-800 text-xs text-zinc-500 text-right">
          {shownCount} wallet{shownCount === 1 ? '' : 's'} · {token} @ {fmtUsd(price)}
        </footer>
      {/if}
    </div>
  </div>
{/if}
