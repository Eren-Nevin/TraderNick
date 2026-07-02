<script lang="ts">
  // Backtracker dialog: the wallets whose OPEN position in one token changed most
  // over a lookback ending at the clicked bar. Rows come pre-ranked by |Δ| from
  // /api/hyperliquid/position_change_wallets (signed amounts: long +, short −).
  // Columns: Address (copy / middle-click → wallet page), Change type, Change
  // amount (token + $, with % for inc/dec), New position (token + $), Old
  // unrealized PnL. Headers are client-sortable. Close via ✕ / backdrop / Esc.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';

  type Row = {
    wallet: string;
    amt_old: number; amt_new: number;
    usd_old: number; usd_new: number;
    unrealized_old: number;
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
    onClose: () => void;
  } = $props();

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
    <div class="w-[64rem] max-w-[96vw] max-h-[90vh] bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl flex flex-col text-sm">
      <header class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
        <div class="flex items-center gap-2">
          <span class="text-zinc-300 font-medium">Position changes</span>
          {#if token}<span class="text-zinc-500">·</span><span class="text-zinc-200">{token}</span>{/if}
          {#if lookback}<span class="text-zinc-500">· Δ</span><span class="text-zinc-400">{lookback}</span>{/if}
          {#if timeLabel}<span class="text-zinc-500">to</span><span class="text-zinc-400">{timeLabel} UTC</span>{/if}
        </div>
        <button type="button" class="text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 cursor-pointer" onclick={onClose} aria-label="Close">✕</button>
      </header>
      <div class="px-4 py-2 text-xs text-zinc-500 border-b border-zinc-800">
        <span class="text-zinc-400">Click</span> address to copy ·
        <span class="text-zinc-400">middle-click</span> (or Ctrl-click) to open the wallet page.
        Ranked by |position change|.
      </div>

      <div class="flex-1 overflow-auto scrollbar-none">
        {#if loading}
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
                  <td class="px-3 py-1.5 font-mono text-[11px] text-zinc-400 whitespace-nowrap">
                    {startLabel}<span class="text-zinc-600"> → </span>{endLabel}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>

      {#if rows.length > 0 && !loading}
        <footer class="px-4 py-1.5 border-t border-zinc-800 text-xs text-zinc-500 text-right">
          {rows.length} wallet{rows.length === 1 ? '' : 's'} · {token} @ {fmtUsd(price)}
        </footer>
      {/if}
    </div>
  </div>
{/if}
