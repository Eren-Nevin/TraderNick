<script lang="ts">
  // Sorted-table primitive used by the hl_top_traders chart kind. Each row
  // is one wallet's pre-aggregated performance over the visible window.
  // Wallet column is truncated (0x1234…abcd) with a copy-to-clipboard
  // affordance — the full address clutters the table otherwise.
  //
  // No sortable headers in V1; the server already sorts via the order_by
  // query param, and the chart's settings popover can change that later.

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

  function truncate(addr: string): string {
    if (!addr) return '';
    if (addr.length < 14) return addr;
    return addr.slice(0, 6) + '…' + addr.slice(-4);
  }

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

  async function copy(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
    } catch {
      // older browsers / non-secure context — silently no-op
    }
  }
</script>

<div class="h-full overflow-auto text-xs">
  {#if leaders.length === 0}
    <div class="h-full flex items-center justify-center text-zinc-500">
      No traders in the visible window
    </div>
  {:else}
    <table class="w-full">
      <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
        <tr>
          <th class="text-left px-3 py-1.5 font-normal">#</th>
          <th class="text-left px-3 py-1.5 font-normal">Wallet</th>
          <th class="text-right px-3 py-1.5 font-normal">Net PnL</th>
          <th class="text-right px-3 py-1.5 font-normal">Volume</th>
          <th class="text-right px-3 py-1.5 font-normal">Trades</th>
          <th class="text-left px-3 py-1.5 font-normal">Tags</th>
        </tr>
      </thead>
      <tbody>
        {#each leaders as l, idx (l.wallet)}
          <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
            <td class="px-3 py-1 text-zinc-500">{idx + 1}</td>
            <td class="px-3 py-1">
              <button
                type="button"
                onclick={() => copy(l.wallet as string)}
                class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer"
                title={l.wallet as string}
              >{truncate(l.wallet as string)}</button>
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
