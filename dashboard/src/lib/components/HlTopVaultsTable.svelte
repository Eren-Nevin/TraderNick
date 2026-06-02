<script lang="ts">
  // Top Vaults leaderboard. The sort selector at the top picks the
  // SERVER-SIDE ranking metric (so the top-20 set itself changes when
  // you flip it: top by Net vs top by Commission ranks different
  // vaults). Header clicks then re-sort the returned 20 rows CLIENT-
  // side on any column — handy for cutting the same set by LP count
  // or distributions without a re-fetch.

  type Vault = {
    rank: number;
    vault: string;
    deposits: number;
    withdrawals: number;
    net: number;
    commission: number;
    distributions: number;
    lp_count: number;
    event_count: number;
    open_notional: number;
    unrealized_pnl: number;
    realized_pnl: number;
    total_pnl: number;
    trade_volume: number;
    trade_count_total: number;
    roe: number;
  };
  type OrderBy = 'net' | 'deposits' | 'withdrawals' | 'commission' | 'total_pnl' | 'realized_pnl' | 'roe';

  let {
    vaults = [],
    orderBy = 'net',
    onChangeOrderBy
  }: {
    vaults: Vault[];
    orderBy: OrderBy;
    onChangeOrderBy: (v: OrderBy) => void;
  } = $props();

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
    return sign + '$' + abs.toFixed(0);
  }
  function fmtNum(n: number): string {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }
  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  // Click-to-copy + transient "✓ copied" feedback
  let copiedAddr = $state('');
  async function copyAddr(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
      copiedAddr = addr;
      setTimeout(() => { if (copiedAddr === addr) copiedAddr = ''; }, 1200);
    } catch { /* no-op */ }
  }

  function fmtPct(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
  }
  // Client-side column sort. '' = preserve server order (which already
  // reflects the title-bar orderBy selection).
  type SortKey = '' | 'deposits' | 'withdrawals' | 'net' | 'commission' | 'distributions'
               | 'lp_count' | 'event_count' | 'open_notional' | 'realized_pnl'
               | 'unrealized_pnl' | 'total_pnl' | 'roe';
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
  let sortedVaults = $derived.by(() => {
    if (!sortKey) return vaults;
    const dir = sortDir;
    return [...vaults].sort((a, b) => ((a[sortKey] as number) - (b[sortKey] as number)) * dir);
  });
</script>

<!-- use:stopDragEvents — see actions/stopDragEvents.ts. Svelte 5
     delegates onmousedown to document, which runs after the dnd-
     action listener; a Svelte action bypasses the delegation. -->
<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-500">Top by:</span>
    <select
      value={orderBy}
      onchange={(e) => onChangeOrderBy(e.currentTarget.value as OrderBy)}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
    >
      <option value="net">Net Inflow</option>
      <option value="deposits">Deposits</option>
      <option value="withdrawals">Withdrawals</option>
      <option value="commission">Commission Earned</option>
      <option value="total_pnl">Total PnL</option>
      <option value="realized_pnl">Realized PnL</option>
      <option value="roe">RoE</option>
    </select>
    <span class="text-[10px] text-zinc-600 ml-auto">Click any column header to re-sort the returned set</span>
  </div>
  <div class="flex-1 overflow-auto">
    {#if vaults.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No vault activity in this window</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left  px-3 py-1.5 font-normal">#</th>
            <th class="text-left  px-3 py-1.5 font-normal">Vault</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('deposits')}>Deposits{sortArrow('deposits')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('withdrawals')}>Withdrawals{sortArrow('withdrawals')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('net')}>Net{sortArrow('net')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('lp_count')}>LPs{sortArrow('lp_count')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none border-l border-zinc-800" onclick={() => onSort('open_notional')}>Open Notional{sortArrow('open_notional')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('realized_pnl')}>Realized PnL{sortArrow('realized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('unrealized_pnl')}>Unrealized PnL{sortArrow('unrealized_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('total_pnl')}>Total PnL{sortArrow('total_pnl')}</th>
            <th class="text-right px-3 py-1.5 font-normal cursor-pointer hover:text-zinc-200 select-none" onclick={() => onSort('roe')}>RoE{sortArrow('roe')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedVaults as v, idx (v.vault)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 text-zinc-500">{sortKey ? idx + 1 : v.rank}</td>
              <td class="px-3 py-1">
                <button type="button" onclick={() => copyAddr(v.vault)}
                        title={v.vault + ' — click to copy'}
                        class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer">
                  {copiedAddr === v.vault ? '✓ copied' : truncate(v.vault)}
                </button>
              </td>
              <td class="px-3 py-1 text-right font-mono text-emerald-400">{fmtUsd(v.deposits)}</td>
              <td class="px-3 py-1 text-right font-mono text-rose-400">{fmtUsd(v.withdrawals)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={v.net > 0}
                  class:text-rose-400={v.net < 0}
                  class:text-zinc-400={v.net === 0}
              >{fmtUsd(v.net)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtNum(v.lp_count)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-300 border-l border-zinc-800">{v.open_notional > 0 ? fmtUsd(v.open_notional) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={v.realized_pnl > 0}
                  class:text-rose-400={v.realized_pnl < 0}
                  class:text-zinc-500={v.realized_pnl === 0}
              >{v.realized_pnl !== 0 ? fmtUsd(v.realized_pnl) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={v.unrealized_pnl > 0}
                  class:text-rose-400={v.unrealized_pnl < 0}
                  class:text-zinc-500={v.unrealized_pnl === 0}
              >{v.unrealized_pnl !== 0 ? fmtUsd(v.unrealized_pnl) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={v.total_pnl > 0}
                  class:text-rose-400={v.total_pnl < 0}
                  class:text-zinc-500={v.total_pnl === 0}
              >{v.total_pnl !== 0 ? fmtUsd(v.total_pnl) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={v.roe > 0}
                  class:text-rose-400={v.roe < 0}
                  class:text-zinc-500={v.roe === 0}
              >{fmtPct(v.roe)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
