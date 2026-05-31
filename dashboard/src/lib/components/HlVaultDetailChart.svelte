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
  function fmtTime(t: number): string {
    if (!t) return '—';
    const d = new Date(t * 1000);
    return d.toISOString().slice(5, 16).replace('T', ' ');
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
  let copiedLp = $state('');
  async function copyAddr(addr: string) {
    try {
      await navigator.clipboard.writeText(addr);
      copiedLp = addr;
      setTimeout(() => { if (copiedLp === addr) copiedLp = ''; }, 1200);
    } catch { /* no-op */ }
  }
</script>

<!-- mousedown/touchstart — svelte-dnd-action listens to these, not
     pointerdown. Stopping them keeps drag confined to the title bar. -->
<div class="h-full flex flex-col text-xs"
     onmousedown={(e) => e.stopPropagation()}
     ontouchstart={(e) => e.stopPropagation()}>
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

  <!-- Summary stats strip -->
  {#if entry}
    <div class="grid grid-cols-6 gap-px bg-zinc-800 border-b border-zinc-800 text-[11px]">
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
                        title={e.wallet + ' — click to copy'}
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
