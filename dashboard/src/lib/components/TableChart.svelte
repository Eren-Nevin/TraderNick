<script lang="ts">
  // Sorted-table primitive used by the hl_top_traders chart kind. Each row
  // is one wallet's pre-aggregated performance over the visible window.
  // Wallet column is truncated (0x1234…abcd) with a click-to-copy
  // affordance — the full address clutters the table otherwise.
  //
  // Click a column header (Net PnL / Volume / Trades) to re-sort client-
  // side. The server already pre-sorts by net_pnl DESC; the in-table
  // sort just lets the user re-cut the same row set.

  type Leader = {
    wallet: string;
    net_pnl: number;
    pnl: number;
    fees: number;
    volume: number;
    buy_volume: number;
    sell_volume: number;
    trade_count: number;
    categories: string[];
  };

  let { leaders = [] }: { leaders: Record<string, unknown>[] | Leader[] } = $props();

  function fmtUsd(n: number): string {
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  function fmtNum(n: number): string {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import WalletAddress from '$lib/components/WalletAddress.svelte';

  // Sortable columns. Default '' = server order (net_pnl DESC).
  type SortKey = '' | 'net_pnl' | 'volume' | 'trade_count';
  let sortKey = $state<SortKey>('');
  let sortDir = $state<1 | -1>(-1);
  function onSort(k: SortKey) {
    if (sortKey === k) sortDir = (sortDir === 1 ? -1 : 1);
    else { sortKey = k; sortDir = -1; }
  }
  function sortArrow(k: SortKey): string {
    if (sortKey !== k) return '';
    return sortDir === 1 ? ' ↑' : ' ↓';
  }
  let sortedLeaders = $derived.by(() => {
    const arr = leaders as Leader[];
    if (!sortKey) return arr;
    const dir = sortDir;
    return [...arr].sort((a, b) => (Number(a[sortKey]) - Number(b[sortKey])) * dir);
  });
</script>

<!-- use:stopDragEvents (action) attaches real DOM listeners that fire
     in bubble phase before the chart-card-level dnd-action listener;
     Svelte 5's delegated onmousedown handlers run too late (at the
     document level) to intercept the drag. -->
<div class="h-full overflow-auto scrollbar-none text-xs" use:stopDragEvents>
  {#if (leaders as Leader[]).length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">
      No traders in the visible window
    </div>
  {:else}
    <table class="w-full">
      <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
        <tr>
          <th class="text-left  px-3 py-1.5 font-normal">#</th>
          <th class="text-left  px-3 py-1.5 font-normal">Wallet</th>
          <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('net_pnl')}>Net PnL{sortArrow('net_pnl')}</th>
          <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('volume')}>Volume{sortArrow('volume')}</th>
          <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('trade_count')}>Trades{sortArrow('trade_count')}</th>
          <th class="text-left  px-3 py-1.5 font-normal">Tags</th>
        </tr>
      </thead>
      <tbody>
        {#each sortedLeaders as l, idx (l.wallet)}
          <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
            <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
            <td class="px-3 py-1">
              <WalletAddress address={l.wallet as string} auxKind="arkham" />
            </td>
            <td class="px-3 py-1 text-right font-mono"
                class:text-emerald-400={Number(l.net_pnl) > 0}
                class:text-rose-400={Number(l.net_pnl) < 0}
            >{fmtUsd(Number(l.net_pnl))}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-300">{fmtUsd(Number(l.volume))}</td>
            <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtNum(Number(l.trade_count))}</td>
            <td class="px-3 py-1">
              {#if (l.categories as string[])?.length > 0}
                {#each (l.categories as string[]) as cat (cat)}
                  <span class="inline-block px-1.5 py-0.5 mr-1 text-[10px] uppercase tracking-wider bg-zinc-900 border border-zinc-800 rounded text-zinc-400">{cat}</span>
                {/each}
              {:else}
                <span class="text-zinc-700">—</span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
