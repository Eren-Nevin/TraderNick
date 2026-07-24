<script lang="ts">
  // Price Alert widget — a single Telegram alert TOPIC (editable name, stable
  // UUID) holding a grid of alert conditions. Each square is one condition
  // (% move over a timeframe); all of them fire into this widget's one topic.
  // State lives on the instance (persisted in the page layout) and is synced
  // server-side (debounced) so the monitor cron can evaluate it.
  //
  // Mute: pauses the whole topic (rule disabled server-side) without losing the
  // config or subscribers — for bug fixes / temporary silence.

  import { onMount } from 'svelte';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import type { ChartInstance } from '$lib/components/charts/config';

  let { instance, rosterTokens = [] }: { instance: ChartInstance; rosterTokens?: string[] } = $props();

  type Win = '5m' | '15m' | '30m' | '1h' | '4h' | '1d';
  const WINDOWS: Win[] = ['5m', '15m', '30m', '1h', '4h', '1d'];

  let userBotConfigured = $state<boolean | null>(null);
  let syncMsg = $state<string>('');
  let syncTimer: ReturnType<typeof setTimeout> | undefined;
  let lastSynced = '';

  // Last-fired status (from CH, independent of subscribers), polled.
  let lastFiredAt = $state<number | null>(null);
  let lastMessage = $state<string>('');
  let lastSentCount = $state(0);
  let statusTimer: ReturnType<typeof setInterval> | undefined;

  function relTime(ms: number): string {
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/notifications/rules');
      if (!res.ok) return;
      const rid = instance.notifRuleId ?? instance.id;
      const rule = ((await res.json()).rules ?? []).find((r: { rule_id: string }) => r.rule_id === rid);
      if (rule) {
        lastFiredAt = typeof rule.last_fired_at === 'number' ? rule.last_fired_at : null;
        lastMessage = rule.last_message ?? '';
        lastSentCount = rule.last_sent_count ?? 0;
      }
    } catch {
      /* ignore */
    }
  }

  // ── token picker (collapsible multiselect) ──────────────────────────────
  let tokenPickerOpen = $state(false);
  let tokenSearch = $state('');
  let pickerEl: HTMLElement | undefined = $state();

  let selectedTokens = $derived(
    (instance.notifTokens ?? '')
      .split(',')
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean)
  );
  function setTokens(list: string[]) {
    instance.notifTokens = list.join(', ');
  }
  function addToken(t: string) {
    const s = new Set(selectedTokens);
    s.add(t.toUpperCase());
    setTokens([...s]);
  }
  function removeToken(t: string) {
    setTokens(selectedTokens.filter((x) => x !== t.toUpperCase()));
  }
  let availableFiltered = $derived(
    (rosterTokens ?? [])
      .map((t) => t.toUpperCase())
      .filter((t) => !selectedTokens.includes(t) && t.includes(tokenSearch.toUpperCase()))
      .slice(0, 60)
  );
  function onWindowClick(e: MouseEvent) {
    if (tokenPickerOpen && pickerEl && !pickerEl.contains(e.target as Node)) {
      tokenPickerOpen = false;
      tokenSearch = '';
    }
  }

  // ── alerts ──────────────────────────────────────────────────────────────
  function uid() {
    try {
      return crypto.randomUUID();
    } catch {
      return Math.random().toString(36).slice(2) + Date.now().toString(36);
    }
  }
  function addAlert() {
    const list = instance.notifAlerts ? [...instance.notifAlerts] : [];
    list.push({ id: uid(), threshold: 10, window: '1h', limit: 'all' });
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
        paused: instance.notifMuted === true,
        alerts: (instance.notifAlerts ?? []).map((a) => ({
          id: a.id,
          threshold: Number(a.threshold) || 0,
          window: a.window,
          limit: a.limit ?? 'all'
        }))
      };
      const res = await fetch('/api/notifications/rules', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const n = (instance.notifAlerts ?? []).length;
      syncMsg = instance.notifMuted
        ? `Muted · ${n} alert${n === 1 ? '' : 's'} paused`
        : n ? `Saved · ${n} alert${n === 1 ? '' : 's'} active` : 'Saved · no alerts (inactive)';
    } catch (e) {
      syncMsg = `Save failed (${e})`;
    }
  }

  // Debounced auto-sync whenever name / tokens / mute / alerts change.
  $effect(() => {
    const snap = JSON.stringify({
      t: instance.notifTitle ?? '',
      k: instance.notifTokens ?? '',
      m: instance.notifMuted === true,
      a: instance.notifAlerts ?? []
    });
    if (snap === lastSynced) return;
    lastSynced = snap;
    clearTimeout(syncTimer);
    syncMsg = 'Saving…';
    syncTimer = setTimeout(sync, 600);
  });

  onMount(() => {
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
</script>

<svelte:window onclick={onWindowClick} />

<div class="flex h-full flex-col gap-2 p-3 text-sm text-zinc-200" use:stopDragEvents>
  <!-- Header: mute toggle · editable topic name · token picker -->
  <div class="flex items-center gap-2">
    <button
      type="button"
      class="shrink-0 rounded p-0.5 transition-colors {instance.notifMuted
        ? 'text-amber-500 hover:text-amber-400'
        : 'text-zinc-600 hover:text-zinc-400'}"
      title={instance.notifMuted
        ? 'Muted — notifications paused. Click to resume.'
        : 'Mute this topic (pause notifications without deleting it)'}
      onclick={() => (instance.notifMuted = !instance.notifMuted)}>
      {#if instance.notifMuted}
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="h-4 w-4">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9.143 17.082a24.248 24.248 0 0 0 3.844.148m-3.844-.148a23.856 23.856 0 0 1-5.455-1.31 8.964 8.964 0 0 0 2.3-5.542m3.155 6.852a3 3 0 0 0 5.667 1.97m1.965-2.277L21 21m-4.225-4.225a23.81 23.81 0 0 0 3.536-1.003A8.967 8.967 0 0 1 18 9.75V9a6 6 0 0 0-9.315-5.023M6.53 6.53l10.245 10.245M6.53 6.53 3 3" />
        </svg>
      {:else}
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="h-4 w-4">
          <path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" />
        </svg>
      {/if}
    </button>

    <input
      class="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-base font-semibold text-zinc-100
             hover:border-zinc-700 focus:border-zinc-600 focus:bg-zinc-950 focus:outline-none"
      bind:value={instance.notifTitle}
      placeholder="Alert topic name"
      title="Topic name shown in the Telegram bot. Renaming keeps existing subscribers." />

    <!-- Token picker: collapsed summary that expands to a multiselect on click -->
    <div class="relative shrink-0" bind:this={pickerEl}>
      <button
        type="button"
        class="flex items-center gap-1 rounded border border-zinc-800 bg-zinc-950 px-2 py-1 text-[11px] text-zinc-300 hover:border-zinc-600"
        title="Which tokens this topic watches. None selected = all tokens."
        onclick={() => (tokenPickerOpen = !tokenPickerOpen)}>
        <span>{selectedTokens.length === 0 ? 'All tokens' : `${selectedTokens.length} token${selectedTokens.length === 1 ? '' : 's'}`}</span>
        <span class="text-zinc-600">{tokenPickerOpen ? '▴' : '▾'}</span>
      </button>

      {#if tokenPickerOpen}
        <div class="absolute right-0 z-30 mt-1 w-60 rounded-lg border border-zinc-700 bg-zinc-900 p-2 shadow-xl">
          {#if selectedTokens.length > 0}
            <div class="mb-2 flex flex-wrap gap-1">
              {#each selectedTokens as t (t)}
                <span class="inline-flex items-center gap-1 rounded bg-zinc-800 px-1.5 py-0.5 text-[11px] text-zinc-200">
                  {t}
                  <button type="button" class="text-zinc-500 hover:text-red-400" title="Remove" onclick={() => removeToken(t)}>✕</button>
                </span>
              {/each}
              <button type="button" class="rounded px-1 text-[11px] text-zinc-500 hover:text-zinc-300" onclick={() => setTokens([])}>clear all</button>
            </div>
          {:else}
            <div class="mb-2 text-[11px] text-zinc-500">No tokens selected → watches <span class="text-zinc-300">all tokens</span>.</div>
          {/if}
          <input
            class="mb-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-[11px] text-zinc-100 placeholder-zinc-600 focus:outline-none"
            placeholder="Filter tokens to add…"
            bind:value={tokenSearch} />
          <div class="max-h-40 overflow-y-auto">
            {#each availableFiltered as t (t)}
              <button
                type="button"
                class="block w-full rounded px-2 py-1 text-left text-[11px] text-zinc-300 hover:bg-zinc-800"
                onclick={() => addToken(t)}>+ {t}</button>
            {/each}
            {#if availableFiltered.length === 0}
              <div class="px-2 py-1 text-[11px] text-zinc-600">No matches.</div>
            {/if}
          </div>
        </div>
      {/if}
    </div>
  </div>

  <!-- Alerts grid (dimmed while muted) -->
  <div class="min-h-0 flex-1 overflow-y-auto {instance.notifMuted ? 'opacity-50' : ''}">
    <div class="grid gap-2" style="grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));">
      {#each instance.notifAlerts ?? [] as alert (alert.id)}
        <div class="relative flex min-h-[132px] flex-col items-center justify-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900/60 p-2">
          <button
            type="button"
            class="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded text-zinc-500 hover:bg-zinc-800 hover:text-red-400"
            title="Remove alert"
            onclick={() => removeAlert(alert.id)}>✕</button>
          <div class="flex items-baseline gap-0.5">
            <input
              class="w-11 rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-center text-sm font-semibold text-zinc-100 focus:border-zinc-500 focus:outline-none"
              type="number" min="0.1" step="0.1"
              bind:value={alert.threshold} />
            <span class="text-sm font-semibold text-zinc-400">%</span>
          </div>
          <span class="text-[10px] uppercase tracking-wide text-zinc-600">move over</span>
          <select
            class="rounded border border-zinc-700 bg-zinc-950 px-2 py-0.5 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            bind:value={alert.window}>
            {#each WINDOWS as w (w)}<option value={w}>{w}</option>{/each}
          </select>
          <select
            class="mt-0.5 rounded border border-zinc-800 bg-zinc-950 px-2 py-0.5 text-[11px] text-zinc-400 focus:border-zinc-500 focus:outline-none"
            title="How many tokens to include per side (top gainers AND top losers)"
            bind:value={alert.limit}>
            <option value="all">Report all</option>
            <option value="5">Top 5 / side</option>
            <option value="10">Top 10 / side</option>
            <option value="20">Top 20 / side</option>
          </select>
        </div>
      {/each}

      <!-- Add-alert square -->
      <button
        type="button"
        class="flex min-h-[132px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-zinc-700 text-zinc-500 hover:border-zinc-500 hover:text-zinc-300"
        onclick={addAlert}>
        <span class="text-2xl leading-none">＋</span>
        <span class="text-[11px]">Add alert</span>
      </button>
    </div>
  </div>

  <!-- Footer: last-triggered / subscribe hint + sync status -->
  <div class="flex items-center justify-between gap-2 border-t border-zinc-800 pt-1.5 text-[11px]">
    <span class="min-w-0 flex-1 truncate text-zinc-500">
      {#if lastFiredAt}
        <span title={lastMessage}>Last triggered <span class="text-zinc-300">{relTime(lastFiredAt)}</span>{lastSentCount ? ` · ${lastSentCount} sent` : ''}</span>
      {:else if instance.notifMuted}
        <span class="text-amber-500">Muted</span> — not pushing notifications.
      {:else if userBotConfigured === false}
        ⚠️ User bot not set up (admin → Notifications)
      {:else}
        Not triggered yet · subscribe in the Telegram bot.
      {/if}
    </span>
    <span class="shrink-0 text-zinc-500">{syncMsg}</span>
  </div>
</div>
