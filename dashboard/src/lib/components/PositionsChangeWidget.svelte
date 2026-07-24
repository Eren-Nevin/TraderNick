<script lang="ts">
  // Positions Change widget — a periodic Telegram report of a wallet group's
  // position-change flow (Trading-Pit-Overview data) over a lookback window:
  // the top-N most-positive and most-negative tokens by a criteria (Net Pos
  // Change / Net Open Long / Net Flip), ranked in $ or wallet-count terms. Each
  // token line shows all three metrics as "$value (walletΔ)". Reuses the shared
  // notification topic / mute / sync / last-fired plumbing (notif* fields).

  import { onMount } from 'svelte';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { walletPinsStore } from '$lib/stores/walletPins.svelte';
  import type { ChartInstance } from '$lib/components/charts/config';

  let { instance }: { instance: ChartInstance } = $props();

  const WINDOWS: NonNullable<ChartInstance['pchgWindow']>[] = ['5m', '15m', '30m', '1h', '4h'];
  const CADENCE: NonNullable<ChartInstance['pchgCadence']>[] = ['1m', '5m', '15m', '1h', '4h'];

  let userBotConfigured = $state<boolean | null>(null);
  let syncMsg = $state<string>('');
  let syncTimer: ReturnType<typeof setTimeout> | undefined;
  let lastSynced = '';

  let lastFiredAt = $state<number | null>(null);
  let lastMessage = $state<string>('');
  let statusTimer: ReturnType<typeof setInterval> | undefined;

  function relTime(ms: number): string {
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  async function sync() {
    try {
      const body = {
        rule_id: instance.notifRuleId ?? instance.id,
        title: (instance.notifTitle ?? 'Positions change').trim() || 'Positions change',
        type: 'positions_change',
        paused: instance.notifMuted === true,
        group_id: instance.pchgGroupId ?? '',
        criteria: instance.pchgCriteria ?? 'net_pos_change',
        rank_by: instance.pchgRankBy ?? 'usd',
        top_n: Number(instance.pchgTopN ?? '5'),
        window: instance.pchgWindow ?? '15m',
        cadence: instance.pchgCadence ?? '15m'
      };
      const res = await fetch('/api/notifications/rules', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status}`);
      syncMsg = instance.notifMuted
        ? 'Muted · paused'
        : instance.pchgGroupId ? 'Saved · active' : 'Saved · pick a group';
    } catch (e) {
      syncMsg = `Save failed (${e})`;
    }
  }

  $effect(() => {
    const snap = JSON.stringify({
      t: instance.notifTitle ?? '',
      m: instance.notifMuted === true,
      g: instance.pchgGroupId ?? '',
      c: instance.pchgCriteria ?? 'net_pos_change',
      r: instance.pchgRankBy ?? 'usd',
      n: instance.pchgTopN ?? '5',
      w: instance.pchgWindow ?? '15m',
      cad: instance.pchgCadence ?? '15m'
    });
    if (snap === lastSynced) return;
    lastSynced = snap;
    clearTimeout(syncTimer);
    syncMsg = 'Saving…';
    syncTimer = setTimeout(sync, 600);
  });

  async function fetchStatus() {
    try {
      const res = await fetch('/api/notifications/rules');
      if (!res.ok) return;
      const rid = instance.notifRuleId ?? instance.id;
      const rule = ((await res.json()).rules ?? []).find((r: { rule_id: string }) => r.rule_id === rid);
      if (rule) {
        lastFiredAt = typeof rule.last_fired_at === 'number' ? rule.last_fired_at : null;
        lastMessage = rule.last_message ?? '';
      }
    } catch {
      /* ignore */
    }
  }

  onMount(() => {
    walletPinsStore.hydrate();
    (async () => {
      try {
        const res = await fetch('/api/notifications/bots');
        if (res.ok) userBotConfigured = (await res.json()).user_bot_configured === true;
      } catch {
        userBotConfigured = null;
      }
    })();
    fetchStatus();
    statusTimer = setInterval(fetchStatus, 30000);
    return () => clearInterval(statusTimer);
  });

  const selCls =
    'rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-[11px] text-zinc-100 focus:border-zinc-500 focus:outline-none';
</script>

<div class="flex h-full flex-col gap-1 p-2 text-xs text-zinc-200" use:stopDragEvents>
  <!-- Header: mute toggle + editable topic name -->
  <div class="flex items-center gap-1.5">
    <button
      type="button"
      class="shrink-0 rounded p-0.5 transition-colors {instance.notifMuted
        ? 'text-amber-500 hover:text-amber-400'
        : 'text-zinc-600 hover:text-zinc-400'}"
      title={instance.notifMuted ? 'Muted — click to resume' : 'Mute this topic (pause notifications)'}
      onclick={() => (instance.notifMuted = !instance.notifMuted)}>
      {#if instance.notifMuted}
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="h-3.5 w-3.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.143 17.082a24.248 24.248 0 0 0 3.844.148m-3.844-.148a23.856 23.856 0 0 1-5.455-1.31 8.964 8.964 0 0 0 2.3-5.542m3.155 6.852a3 3 0 0 0 5.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 0 0 3.536-1.003A8.967 8.967 0 0 1 18 9.75V9a6 6 0 0 0-9.315-5.023M6.53 6.53l10.245 10.245M6.53 6.53 3 3" /></svg>
      {:else}
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="h-3.5 w-3.5"><path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" /></svg>
      {/if}
    </button>
    <input
      class="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-sm font-semibold text-zinc-100 hover:border-zinc-700 focus:border-zinc-600 focus:bg-zinc-950 focus:outline-none"
      bind:value={instance.notifTitle}
      placeholder="Alert topic name"
      title="Topic name shown in the Telegram bot. Renaming keeps existing subscribers." />
  </div>

  <!-- Settings -->
  <div class="min-h-0 flex-1 overflow-y-auto">
    <div class="grid grid-cols-2 gap-x-2 gap-y-1">
      <label class="col-span-2 text-[10px] text-zinc-400">
        Wallet group
        <select class="{selCls} mt-0.5 block w-full"
          value={instance.pchgGroupId ?? ''}
          onchange={(e) => (instance.pchgGroupId = e.currentTarget.value || null)}>
          <option value="">— select a group —</option>
          {#each walletPinsStore.groups as g (g.id)}<option value={g.id}>{g.name}</option>{/each}
        </select>
      </label>

      <label class="text-[10px] text-zinc-400">
        Rank by
        <select class="{selCls} mt-0.5 block w-full" bind:value={instance.pchgCriteria}>
          <option value="net_pos_change">Net Pos Change</option>
          <option value="net_open_long">Net Open Long</option>
          <option value="net_flip">Net Flip</option>
        </select>
      </label>

      <label class="text-[10px] text-zinc-400">
        In terms of
        <select class="{selCls} mt-0.5 block w-full" bind:value={instance.pchgRankBy}>
          <option value="usd">$ amount</option>
          <option value="wallets">Wallets</option>
        </select>
      </label>

      <label class="text-[10px] text-zinc-400">
        Top N / side
        <select class="{selCls} mt-0.5 block w-full" bind:value={instance.pchgTopN}>
          <option value="3">3</option><option value="5">5</option>
          <option value="10">10</option><option value="20">20</option>
        </select>
      </label>

      <label class="text-[10px] text-zinc-400">
        Window
        <select class="{selCls} mt-0.5 block w-full" bind:value={instance.pchgWindow}>
          {#each WINDOWS as w (w)}<option value={w}>{w}</option>{/each}
        </select>
      </label>

      <label class="text-[10px] text-zinc-400">
        Report every
        <select class="{selCls} mt-0.5 block w-full" bind:value={instance.pchgCadence}>
          {#each CADENCE as c (c)}<option value={c}>{c}</option>{/each}
        </select>
      </label>
    </div>
  </div>

  <!-- Footer -->
  <div class="flex items-center justify-between gap-2 border-t border-zinc-800 pt-1 text-[10px]">
    <span class="min-w-0 flex-1 truncate text-zinc-500">
      {#if lastFiredAt}
        <span title={lastMessage}>Last sent <span class="text-zinc-300">{relTime(lastFiredAt)}</span></span>
      {:else if instance.notifMuted}
        <span class="text-amber-500">Muted</span> — not pushing.
      {:else if userBotConfigured === false}
        ⚠️ User bot not set up (admin → Notifications)
      {:else}
        Not sent yet · subscribe in the Telegram bot.
      {/if}
    </span>
    <span class="shrink-0 text-zinc-500">{syncMsg}</span>
  </div>
</div>
