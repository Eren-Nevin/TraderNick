<script lang="ts">
  // Top Vaults leaderboard. Sort selector at the top picks the ranking
  // metric — by net inflow, raw deposits, raw withdrawals, or commission
  // earned by the vault leader. Switching the sort triggers a re-fetch
  // server-side; the table itself is static once the data lands.

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
  };
  type OrderBy = 'net' | 'deposits' | 'withdrawals' | 'commission';

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
  async function copyAddr(addr: string) {
    try { await navigator.clipboard.writeText(addr); } catch { /* no-op */ }
  }
</script>

<div class="h-full flex flex-col text-xs">
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-500">Sort by:</span>
    <select
      value={orderBy}
      onchange={(e) => onChangeOrderBy(e.currentTarget.value as OrderBy)}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-medium text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500"
    >
      <option value="net">Net Inflow</option>
      <option value="deposits">Deposits</option>
      <option value="withdrawals">Withdrawals</option>
      <option value="commission">Commission Earned</option>
    </select>
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
            <th class="text-right px-3 py-1.5 font-normal">Deposits</th>
            <th class="text-right px-3 py-1.5 font-normal">Withdrawals</th>
            <th class="text-right px-3 py-1.5 font-normal">Net</th>
            <th class="text-right px-3 py-1.5 font-normal">Commission</th>
            <th class="text-right px-3 py-1.5 font-normal">Distributions</th>
            <th class="text-right px-3 py-1.5 font-normal">LPs</th>
            <th class="text-right px-3 py-1.5 font-normal">Events</th>
          </tr>
        </thead>
        <tbody>
          {#each vaults as v (v.vault)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 text-zinc-500">{v.rank}</td>
              <td class="px-3 py-1">
                <button type="button" onclick={() => copyAddr(v.vault)}
                        title={v.vault + ' — click to copy'}
                        class="font-mono text-zinc-200 hover:text-blue-400 cursor-pointer">
                  {truncate(v.vault)}
                </button>
              </td>
              <td class="px-3 py-1 text-right font-mono text-emerald-400">{fmtUsd(v.deposits)}</td>
              <td class="px-3 py-1 text-right font-mono text-rose-400">{fmtUsd(v.withdrawals)}</td>
              <td class="px-3 py-1 text-right font-mono"
                  class:text-emerald-400={v.net > 0}
                  class:text-rose-400={v.net < 0}
                  class:text-zinc-400={v.net === 0}
              >{fmtUsd(v.net)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-200">{fmtUsd(v.commission)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtUsd(v.distributions)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{fmtNum(v.lp_count)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-500">{fmtNum(v.event_count)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
