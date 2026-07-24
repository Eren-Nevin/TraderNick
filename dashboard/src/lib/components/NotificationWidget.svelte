<script lang="ts">
  // NotificationWidget — a user-defined alert. Phase 1: "Price Change" (any of
  // the watched tokens moving ≥ threshold% over a window). The rule is synced
  // server-side (to notification_rules + a 1:1 topic keyed by this widget's
  // instance id) so the monitor cron can evaluate it and push Telegram alerts
  // to the topic's subscribers. The instance.notif* fields also persist in the
  // page's localStorage layout for UI state.

  import { onMount } from 'svelte';
  import type { ChartInstance } from '$lib/components/charts/config';

  let { instance }: { instance: ChartInstance } = $props();

  let saving = $state(false);
  let saveMsg = $state<string | null>(null);
  let userBotConfigured = $state<boolean | null>(null);

  const WINDOW_S: Record<string, number> = { '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 };
  const CADENCE_S: Record<string, number> = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600 };
  const COOLDOWN_S: Record<string, number> = { '0': 0, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 };

  onMount(async () => {
    try {
      const res = await fetch('/api/notifications/bots');
      if (res.ok) userBotConfigured = (await res.json()).user_bot_configured === true;
    } catch {
      userBotConfigured = null;
    }
  });

  async function save() {
    saving = true;
    saveMsg = null;
    try {
      const body = {
        rule_id: instance.notifRuleId ?? instance.id,
        title: (instance.notifTitle ?? 'Price alert').trim() || 'Price alert',
        type: 'price_change',
        enabled: instance.notifEnabled === true,
        threshold_pct: Number(instance.notifThreshold ?? 10),
        window_s: WINDOW_S[instance.notifWindow ?? '1h'] ?? 3600,
        tokens: instance.notifTokens ?? '',
        cadence_s: CADENCE_S[instance.notifCadence ?? '5m'] ?? 300,
        cooldown_s: COOLDOWN_S[instance.notifCooldown ?? '1h'] ?? 3600
      };
      const res = await fetch('/api/notifications/rules', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      saveMsg = instance.notifEnabled ? 'Saved & active.' : 'Saved (disabled).';
    } catch (e) {
      saveMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }
</script>

<div class="flex h-full flex-col gap-3 p-3 text-sm text-zinc-200">
  <div class="flex items-center justify-between">
    <span class="rounded bg-zinc-800 px-2 py-0.5 text-[11px] uppercase tracking-wide text-zinc-400">Price change alert</span>
    <label class="flex items-center gap-1.5 text-xs">
      <input type="checkbox" bind:checked={instance.notifEnabled} />
      <span class={instance.notifEnabled ? 'text-emerald-400' : 'text-zinc-500'}>
        {instance.notifEnabled ? 'active' : 'off'}
      </span>
    </label>
  </div>

  <label class="text-xs text-zinc-400">
    Alert name (topic)
    <input
      class="mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
      bind:value={instance.notifTitle} placeholder="e.g. Majors 10% move" />
  </label>

  <div class="flex flex-wrap gap-3">
    <label class="text-xs text-zinc-400">
      Move ≥ (%)
      <input
        class="mt-1 block w-20 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
        type="number" min="0.1" step="0.1" bind:value={instance.notifThreshold} />
    </label>
    <label class="text-xs text-zinc-400">
      Over
      <select
        class="mt-1 block rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
        bind:value={instance.notifWindow}>
        <option value="15m">15m</option><option value="30m">30m</option>
        <option value="1h">1h</option><option value="4h">4h</option><option value="1d">1d</option>
      </select>
    </label>
    <label class="text-xs text-zinc-400">
      Check every
      <select
        class="mt-1 block rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
        bind:value={instance.notifCadence}>
        <option value="1m">1m</option><option value="5m">5m</option>
        <option value="15m">15m</option><option value="1h">1h</option>
      </select>
    </label>
    <label class="text-xs text-zinc-400">
      Re-alert after
      <select
        class="mt-1 block rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
        bind:value={instance.notifCooldown}>
        <option value="0">once</option><option value="15m">15m</option>
        <option value="1h">1h</option><option value="4h">4h</option><option value="1d">1d</option>
      </select>
    </label>
  </div>

  <label class="text-xs text-zinc-400">
    Tokens (comma-separated; blank = all)
    <input
      class="mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
      bind:value={instance.notifTokens} placeholder="BTC, ETH, SOL" />
  </label>

  <div class="mt-auto flex items-center justify-between gap-2">
    <span class="text-[11px] text-zinc-500">
      {#if userBotConfigured === false}
        ⚠️ User bot not set up (admin → Notifications)
      {:else}
        Subscribe to this alert inside the Telegram user bot.
      {/if}
    </span>
    <div class="flex items-center gap-2">
      {#if saveMsg}<span class="text-[11px] text-zinc-400">{saveMsg}</span>{/if}
      <button
        class="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-600 disabled:opacity-50"
        disabled={saving}
        onclick={save}>Save</button>
    </div>
  </div>
</div>
