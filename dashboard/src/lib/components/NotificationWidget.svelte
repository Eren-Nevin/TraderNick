<script lang="ts">
  // Price Alert widget — a single Telegram alert TOPIC (editable name, stable
  // UUID) holding a grid of alert conditions. Each square is one condition
  // (% move over a timeframe); all of them fire into this widget's one topic.
  // State lives on the instance (persisted in the page layout) and is synced
  // server-side (debounced) so the monitor cron can evaluate it.

  import { onMount } from 'svelte';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import type { ChartInstance } from '$lib/components/charts/config';

  let { instance }: { instance: ChartInstance } = $props();

  type Win = '5m' | '15m' | '30m' | '1h' | '4h' | '1d';
  const WINDOWS: Win[] = ['5m', '15m', '30m', '1h', '4h', '1d'];

  let userBotConfigured = $state<boolean | null>(null);
  let syncMsg = $state<string>('');
  let syncTimer: ReturnType<typeof setTimeout> | undefined;
  let lastSynced = '';

  function uid() {
    try {
      return crypto.randomUUID();
    } catch {
      return Math.random().toString(36).slice(2) + Date.now().toString(36);
    }
  }

  function addAlert() {
    const list = instance.notifAlerts ? [...instance.notifAlerts] : [];
    list.push({ id: uid(), threshold: 10, window: '1h' });
    instance.notifAlerts = list;
  }
  function removeAlert(id: string) {
    instance.notifAlerts = (instance.notifAlerts ?? []).filter((a) => a.id !== id);
  }

  async function sync() {
    try {
      const body = {
        rule_id: instance.notifRuleId ?? instance.id,
        title: (instance.notifTitle ?? 'Price alert').trim() || 'Price alert',
        tokens: instance.notifTokens ?? '',
        alerts: (instance.notifAlerts ?? []).map((a) => ({
          id: a.id,
          threshold: Number(a.threshold) || 0,
          window: a.window
        }))
      };
      const res = await fetch('/api/notifications/rules', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const n = (instance.notifAlerts ?? []).length;
      syncMsg = n ? `Saved · ${n} alert${n === 1 ? '' : 's'} active` : 'Saved · no alerts (inactive)';
    } catch (e) {
      syncMsg = `Save failed (${e})`;
    }
  }

  // Debounced auto-sync whenever the topic name / tokens / alerts change.
  $effect(() => {
    const snap = JSON.stringify({
      t: instance.notifTitle ?? '',
      k: instance.notifTokens ?? '',
      a: instance.notifAlerts ?? []
    });
    if (snap === lastSynced) return;
    lastSynced = snap;
    clearTimeout(syncTimer);
    syncMsg = 'Saving…';
    syncTimer = setTimeout(sync, 600);
  });

  onMount(async () => {
    try {
      const res = await fetch('/api/notifications/bots');
      if (res.ok) userBotConfigured = (await res.json()).user_bot_configured === true;
    } catch {
      userBotConfigured = null;
    }
  });
</script>

<div class="flex h-full flex-col gap-2 p-3 text-sm text-zinc-200" use:stopDragEvents>
  <!-- Topic header: editable name (mirrored in the Telegram bot) -->
  <div class="flex items-center gap-2">
    <span class="text-base leading-none" title="Telegram alert topic">🔔</span>
    <input
      class="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-base font-semibold text-zinc-100
             hover:border-zinc-700 focus:border-zinc-600 focus:bg-zinc-950 focus:outline-none"
      bind:value={instance.notifTitle}
      placeholder="Alert topic name"
      title="Topic name shown in the Telegram bot. Renaming keeps existing subscribers." />
    <input
      class="w-28 shrink-0 rounded border border-zinc-800 bg-zinc-950 px-2 py-1 text-[11px] text-zinc-300 placeholder-zinc-600"
      bind:value={instance.notifTokens}
      placeholder="all tokens"
      title="Comma-separated token filter for this topic. Blank = every token." />
  </div>

  <!-- Alerts grid -->
  <div class="min-h-0 flex-1 overflow-y-auto">
    <div class="grid gap-2" style="grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));">
      {#each instance.notifAlerts ?? [] as alert (alert.id)}
        <div class="relative flex flex-col items-center justify-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900/60 p-2"
             style="aspect-ratio: 1 / 1;">
          <button
            type="button"
            class="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
            title="Remove alert"
            onclick={() => removeAlert(alert.id)}>✕</button>
          <div class="flex items-baseline gap-0.5">
            <input
              class="w-12 rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-center text-lg font-semibold text-zinc-100 focus:border-zinc-500 focus:outline-none"
              type="number" min="0.1" step="0.1"
              bind:value={alert.threshold} />
            <span class="text-lg font-semibold text-zinc-400">%</span>
          </div>
          <span class="text-[10px] uppercase tracking-wide text-zinc-600">move over</span>
          <select
            class="rounded border border-zinc-700 bg-zinc-950 px-2 py-0.5 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            bind:value={alert.window}>
            {#each WINDOWS as w (w)}<option value={w}>{w}</option>{/each}
          </select>
        </div>
      {/each}

      <!-- Add-alert square -->
      <button
        type="button"
        class="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-zinc-700 text-zinc-500 hover:border-zinc-500 hover:text-zinc-300"
        style="aspect-ratio: 1 / 1;"
        onclick={addAlert}>
        <span class="text-2xl leading-none">＋</span>
        <span class="text-[11px]">Add alert</span>
      </button>
    </div>
  </div>

  <!-- Footer: sync status + subscribe hint -->
  <div class="flex items-center justify-between gap-2 border-t border-zinc-800 pt-1.5 text-[11px]">
    <span class="text-zinc-500">
      {#if userBotConfigured === false}
        ⚠️ User bot not set up (admin → Notifications)
      {:else}
        Subscribe to “{(instance.notifTitle ?? 'Price alert').trim() || 'Price alert'}” in the Telegram bot.
      {/if}
    </span>
    <span class="shrink-0 text-zinc-500">{syncMsg}</span>
  </div>
</div>
