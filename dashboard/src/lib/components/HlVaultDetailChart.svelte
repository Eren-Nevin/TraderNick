<script lang="ts">
  // Vault Detail view — pick a vault from the top-N (by gross flow) and
  // see its summary stats + recent activity log. One fetch hydrates
  // everything, so flipping the vault selector is instant.

  type Event = {
    time: number;
    wallet: string;
    action: string;
    amount: number;
    commission: number;
    fee: number;
  };
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
    first_event_at: number;
    last_event_at: number;
    open_notional: number;
    unrealized_pnl: number;
    realized_pnl: number;
    total_pnl: number;
    trade_volume: number;
    trade_count_total: number;
    roe: number;
    events: Event[];
  };

  let {
    vaults = [],
    selectedVault = '',
    onSelectVault
  }: {
    vaults: Vault[];
    selectedVault: string;
    onSelectVault: (v: string) => void;
  } = $props();

  let effective = $derived.by(() => {
    if (vaults.length === 0) return '';
    const found = vaults.find((v) => v.vault.toLowerCase() === selectedVault.toLowerCase());
    return found ? found.vault : vaults[0].vault;
  });
  let entry = $derived(vaults.find((v) => v.vault === effective) ?? null);

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
  function fmtPct(n: number): string {
    if (!isFinite(n) || n === 0) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
  }
  function fmtTime(t: number): string {
    if (!t) return '—';
    // "MM-DD HH:MM" in the active display zone (strip the year from the shared helper).
    return fmtTzDateTime(t).slice(5);
  }

  let copied = $state(false);
  async function copyVault() {
    if (!effective) return;
    try {
      await navigator.clipboard.writeText(effective);
      copied = true;
      setTimeout(() => (copied = false), 1200);
    } catch { /* no-op */ }
  }
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { onAuxClickArkham, onMouseDownSuppressMiddle } from '$lib/arkham';
  import { fmtTzDateTime } from '$lib/stores/timezone.svelte';

  let copiedLp = $state('');
  async function copyAddr(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
      copiedLp = addr;
      setTimeout(() => { if (copiedLp === addr) copiedLp = ''; }, 1200);
    } catch { /* no-op */ }
  }
</script>

<!-- use:stopDragEvents — see actions/stopDragEvents.ts. -->
<div class="h-full flex flex-col text-xs" use:stopDragEvents>
  <!-- Vault picker -->
  <div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950">
    <span class="text-zinc-500">Vault:</span>
    <select
      value={effective}
      onchange={(e) => onSelectVault(e.currentTarget.value)}
      disabled={vaults.length === 0}
      class="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs font-mono text-zinc-100 hover:border-zinc-600 focus:outline-none focus:border-zinc-500 flex-1 min-w-0"
    >
      {#each vaults as v (v.vault)}
        <option value={v.vault}>#{v.rank} · {truncate(v.vault)} · gross {fmtUsd(v.deposits + v.withdrawals)}</option>
      {/each}
      {#if vaults.length === 0}<option value="">— no data —</option>{/if}
    </select>
    <button
      type="button" onclick={copyVault} disabled={!effective}
      title="Copy vault address"
      class="px-2 py-1 rounded-md border border-zinc-700 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 disabled:opacity-40"
    >{copied ? '✓ copied' : 'Copy'}</button>
  </div>

  <!-- Stats strip — two rows: Flow (deposit/withdraw/net/commission/
       distributions/LPs) then Performance (PnL & RoE from positions +
       realized trades). Performance row shows '—' for vaults that only
       have deposit/withdraw activity and no actual trading. -->
  {#if entry}
    <div class="border-b border-zinc-800 text-[11px]">
      <div class="px-3 pt-1.5 pb-0.5 text-[9px] uppercase tracking-widest text-zinc-600 bg-zinc-950">Flow</div>
      <div class="grid grid-cols-6 gap-px bg-zinc-800">
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Deposits</div>
          <div class="font-mono text-emerald-400">{fmtUsd(entry.deposits)}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Withdrawals</div>
          <div class="font-mono text-rose-400">{fmtUsd(entry.withdrawals)}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Net</div>
          <div class="font-mono" class:text-emerald-400={entry.net > 0} class:text-rose-400={entry.net < 0}>{fmtUsd(entry.net)}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Commission</div>
          <div class="font-mono text-zinc-200">{fmtUsd(entry.commission)}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Distributions</div>
          <div class="font-mono text-zinc-200">{fmtUsd(entry.distributions)}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">LPs · Events</div>
          <div class="font-mono text-zinc-300">{entry.lp_count} · {entry.event_count}</div>
        </div>
      </div>
      <div class="px-3 pt-1.5 pb-0.5 text-[9px] uppercase tracking-widest text-zinc-600 bg-zinc-950 border-t border-zinc-900">Performance</div>
      <div class="grid grid-cols-6 gap-px bg-zinc-800">
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Open Notional</div>
          <div class="font-mono text-zinc-200">{entry.open_notional > 0 ? fmtUsd(entry.open_notional) : '—'}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Realized PnL</div>
          <div class="font-mono" class:text-emerald-400={entry.realized_pnl > 0} class:text-rose-400={entry.realized_pnl < 0} class:text-zinc-500={entry.realized_pnl === 0}>{entry.realized_pnl !== 0 ? fmtUsd(entry.realized_pnl) : '—'}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Unrealized PnL</div>
          <div class="font-mono" class:text-emerald-400={entry.unrealized_pnl > 0} class:text-rose-400={entry.unrealized_pnl < 0} class:text-zinc-500={entry.unrealized_pnl === 0}>{entry.unrealized_pnl !== 0 ? fmtUsd(entry.unrealized_pnl) : '—'}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Total PnL</div>
          <div class="font-mono" class:text-emerald-400={entry.total_pnl > 0} class:text-rose-400={entry.total_pnl < 0} class:text-zinc-500={entry.total_pnl === 0}>{entry.total_pnl !== 0 ? fmtUsd(entry.total_pnl) : '—'}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">RoE</div>
          <div class="font-mono" class:text-emerald-400={entry.roe > 0} class:text-rose-400={entry.roe < 0} class:text-zinc-500={entry.roe === 0}>{fmtPct(entry.roe)}</div>
        </div>
        <div class="bg-zinc-950 px-3 py-1.5">
          <div class="text-zinc-500">Volume · Trades</div>
          <div class="font-mono text-zinc-300">{entry.trade_volume > 0 ? fmtUsd(entry.trade_volume) : '—'} · {entry.trade_count_total}</div>
        </div>
      </div>
    </div>
  {/if}

  <!-- Recent activity log -->
  <div class="flex-1 overflow-auto">
    {#if !entry || entry.events.length === 0}
      <div class="h-full flex items-center justify-center text-zinc-500">No recent events</div>
    {:else}
      <table class="w-full">
        <thead class="sticky top-0 bg-zinc-950 text-zinc-500 border-b border-zinc-800">
          <tr>
            <th class="text-left  px-3 py-1.5 font-normal">Time</th>
            <th class="text-left  px-3 py-1.5 font-normal">Action</th>
            <th class="text-left  px-3 py-1.5 font-normal">LP</th>
            <th class="text-right px-3 py-1.5 font-normal">Amount</th>
            <th class="text-right px-3 py-1.5 font-normal">Commission</th>
            <th class="text-right px-3 py-1.5 font-normal">Fee</th>
          </tr>
        </thead>
        <tbody>
          {#each entry.events as e (e.time + '|' + e.wallet + '|' + e.action)}
            <tr class="border-b border-zinc-900 hover:bg-zinc-900/40">
              <td class="px-3 py-1 font-mono text-zinc-400">{fmtTime(e.time)}</td>
              <td class="px-3 py-1">
                <span class="font-mono uppercase text-[10px] tracking-wider px-1.5 py-0.5 rounded border"
                      class:text-emerald-300={e.action === 'deposit'}
                      class:border-emerald-900={e.action === 'deposit'}
                      class:text-rose-300={e.action === 'withdraw'}
                      class:border-rose-900={e.action === 'withdraw'}
                      class:text-amber-300={e.action === 'distribution'}
                      class:border-amber-900={e.action === 'distribution'}
                      class:text-zinc-400={e.action === 'create'}
                      class:border-zinc-700={e.action === 'create'}
                >{e.action}</span>
              </td>
              <td class="px-3 py-1">
                <button type="button" onclick={() => copyAddr(e.wallet)}
                        onauxclick={onAuxClickArkham(e.wallet)}
                        onmousedown={onMouseDownSuppressMiddle}
                        title={e.wallet + ' — click to copy · middle-click to open in Arkham'}
                        class="font-mono text-zinc-300 hover:text-blue-400 cursor-pointer">
                  {copiedLp === e.wallet ? '✓ copied' : truncate(e.wallet)}
                </button>
              </td>
              <td class="px-3 py-1 text-right font-mono text-zinc-200">{fmtUsd(e.amount)}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-400">{e.commission > 0 ? fmtUsd(e.commission) : '—'}</td>
              <td class="px-3 py-1 text-right font-mono text-zinc-500">{e.fee > 0 ? fmtUsd(e.fee) : '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>
