<script lang="ts">
  // Modular Token Leaderboard widget — a composite notification. The user adds
  // MODULES (each = one of the other notif kinds: Price Move / Positions /
  // Positions Change / Spot-VD); the monitor evaluates every module and returns
  // the INTERSECTION of their long sets and of their short sets. Shared Top-N +
  // cadence. Columns (≤4) are picked from the modules' outputs (default none →
  // token names only). Reuses the shared notif topic / mute / sync / last-fired
  // plumbing. Because it's an AND, the longs/shorts counts can be asymmetric.

  import { onMount } from 'svelte';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import { walletPinsStore } from '$lib/stores/walletPins.svelte';
  import type { ChartInstance, ModularModule } from '$lib/components/charts/config';

  let { instance }: { instance: ChartInstance } = $props();

  const TOPN: NonNullable<ChartInstance['mlTopN']>[] = ['3', '5', '10', '20'];
  const CADENCE: NonNullable<ChartInstance['mlCadence']>[] = ['1m', '5m', '15m', '1h'];
  const PM_WINDOWS: NonNullable<ModularModule['window']>[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];
  const PA_STALE: NonNullable<ModularModule['staleness']>[] = ['1h', '4h', '1d', '3d', '7d', '14d', '30d'];
  const PC_WINDOWS: NonNullable<ModularModule['pcWindow']>[] = ['5m', '15m', '30m', '1h', '4h'];
  const SV_LOOKBACKS: NonNullable<ModularModule['svLookback']>[] = ['15m', '30m', '1h', '4h', '12h', '1d', '7d'];
  const MAX_COLS = 4;

  const TYPE_LABEL: Record<ModularModule['type'], string> = {
    price_move: 'Price Move',
    positions: 'Positions',
    positions_change: 'Positions Change',
    spot_vd: 'Spot-VD'
  };

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

  function uid() {
    try { return crypto.randomUUID(); } catch { return Math.random().toString(36).slice(2) + Date.now().toString(36); }
  }

  // ── module list ──────────────────────────────────────────────────────────
  const modules = $derived(instance.mlModules ?? []);
  let addPickerOpen = $state(false);

  function defaultsFor(type: ModularModule['type']): ModularModule {
    const base = { id: uid(), type } as ModularModule;
    if (type === 'price_move') return { ...base, threshold: 1, window: '1h' };
    if (type === 'positions') return { ...base, groupId: null, posCriteria: 'net_long', staleness: '1d' };
    if (type === 'positions_change') return { ...base, groupId: null, pcCriteria: 'net_pos_change', pcWindow: '15m', pcRankBy: 'usd' };
    return { ...base, svCriteria: 'spot_vd_pct', svLookback: '1h' };
  }
  function addModule(type: ModularModule['type']) {
    instance.mlModules = [...modules, defaultsFor(type)];
    addPickerOpen = false;
  }
  function removeModule(id: string) {
    instance.mlModules = modules.filter((m) => m.id !== id);
    instance.mlColumns = (instance.mlColumns ?? []).filter((c) => !c.startsWith(`${id}:`));
    if (instance.mlPrimary === id) instance.mlPrimary = '';
  }
  // reassign triggers Svelte reactivity after mutating a module field in place
  function touch() { instance.mlModules = [...modules]; }

  function moduleLabel(m: ModularModule, i: number): string {
    const g = (id: string | null | undefined) => walletPinsStore.groups.find((x) => x.id === id)?.name ?? '—';
    let d = '';
    if (m.type === 'price_move') d = `${m.threshold ?? 0}% / ${m.window ?? '1h'}`;
    else if (m.type === 'positions') d = `${g(m.groupId)} · ${m.posCriteria === 'net_size' ? 'Net Size' : 'Net Long'}`;
    else if (m.type === 'positions_change') d = `${g(m.groupId)} · ${m.pcCriteria ?? 'net_pos_change'} · ${m.pcWindow ?? '15m'}`;
    else d = `${m.svCriteria === 'vol_pct' ? 'Vol Δ%' : 'Spot VD %'} · ${m.svLookback ?? '1h'}`;
    return `${i + 1}. ${TYPE_LABEL[m.type]} — ${d}`;
  }

  // ── columns ──────────────────────────────────────────────────────────────
  type Col = { id: string; label: string };
  function moduleCols(m: ModularModule): Col[] {
    if (m.type === 'price_move') return [{ id: `${m.id}:dpct`, label: 'Δ%' }];
    if (m.type === 'positions') return [
      { id: `${m.id}:net_long`, label: 'Net Long' },
      { id: `${m.id}:net_size`, label: 'Net Size' },
      { id: `${m.id}:ls`, label: 'L/S' }
    ];
    if (m.type === 'positions_change') return [
      { id: `${m.id}:net_pos_change`, label: 'Pos Δ' },
      { id: `${m.id}:net_open_long`, label: 'Open L' },
      { id: `${m.id}:net_flip`, label: 'Flip' }
    ];
    return [
      { id: `${m.id}:spot_vd_pct`, label: 'Spot VD %' },
      { id: `${m.id}:vol_pct`, label: 'Vol Δ%' }
    ];
  }
  const selectedCols = $derived(instance.mlColumns ?? []);
  const colFull = $derived(selectedCols.length >= MAX_COLS);
  function toggleCol(id: string) {
    const cur = selectedCols;
    if (cur.includes(id)) instance.mlColumns = cur.filter((c) => c !== id);
    else if (cur.length < MAX_COLS) instance.mlColumns = [...cur, id];
  }

  async function sync() {
    try {
      const body = {
        rule_id: instance.notifRuleId ?? instance.id,
        title: (instance.notifTitle ?? 'Modular leaderboard').trim() || 'Modular leaderboard',
        type: 'modular_alert',
        paused: instance.notifMuted === true,
        top_n: Number(instance.mlTopN ?? '10'),
        cadence: instance.mlCadence ?? '5m',
        primary: instance.mlPrimary ?? '',
        columns: instance.mlColumns ?? [],
        modules
      };
      const res = await fetch('/api/notifications/rules', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const j = await res.json();
      syncMsg = instance.notifMuted
        ? 'Muted · paused'
        : !modules.length ? 'Add a module'
        : j.incomplete ? 'Paused · a module needs a group'
        : `Saved · ${j.modules} module${j.modules === 1 ? '' : 's'} (AND)`;
    } catch (e) {
      syncMsg = `Save failed (${e})`;
    }
  }

  $effect(() => {
    const snap = JSON.stringify({
      t: instance.notifTitle ?? '',
      m: instance.notifMuted === true,
      n: instance.mlTopN ?? '10',
      c: instance.mlCadence ?? '5m',
      p: instance.mlPrimary ?? '',
      col: instance.mlColumns ?? [],
      mods: modules
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
    } catch { /* ignore */ }
  }

  onMount(() => {
    walletPinsStore.hydrate();
    (async () => {
      try {
        const res = await fetch('/api/notifications/bots');
        if (res.ok) userBotConfigured = (await res.json()).user_bot_configured === true;
      } catch { userBotConfigured = null; }
    })();
    fetchStatus();
    statusTimer = setInterval(fetchStatus, 30000);
    return () => clearInterval(statusTimer);
  });

  const sel = 'rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 text-[11px] text-zinc-100 focus:border-zinc-500 focus:outline-none';
  const lbl = 'text-[9px] uppercase tracking-wide text-zinc-500';
</script>

<div class="flex h-full flex-col gap-1.5 p-2 text-xs text-zinc-200" use:stopDragEvents>
  <!-- Header: mute + editable topic name -->
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

  <!-- Shared controls -->
  <div class="flex flex-wrap items-end gap-2 border-b border-zinc-800 pb-1.5">
    <label class="flex flex-col gap-0.5">
      <span class={lbl}>Top N / side</span>
      <select class={sel} bind:value={instance.mlTopN}>
        {#each TOPN as n (n)}<option value={n}>{n}</option>{/each}
      </select>
    </label>
    <label class="flex flex-col gap-0.5">
      <span class={lbl}>Cadence</span>
      <select class={sel} bind:value={instance.mlCadence}>
        {#each CADENCE as c (c)}<option value={c}>{c}</option>{/each}
      </select>
    </label>
    <label class="flex min-w-0 flex-1 flex-col gap-0.5">
      <span class={lbl}>Rank final by</span>
      <select class="{sel} w-full" bind:value={instance.mlPrimary}>
        <option value="">Auto (first module)</option>
        {#each modules as m, i (m.id)}<option value={m.id}>{moduleLabel(m, i)}</option>{/each}
      </select>
    </label>
  </div>

  <!-- Scrollable body: modules + columns -->
  <div class="min-h-0 flex-1 overflow-y-auto {instance.notifMuted ? 'opacity-50' : ''}">
    <!-- Modules -->
    <div class="mb-1 flex items-center justify-between">
      <span class="text-[10px] font-semibold uppercase tracking-wide text-zinc-400">Modules (AND)</span>
      <div class="relative">
        <button type="button" class="rounded border border-dashed border-zinc-600 px-1.5 py-0.5 text-[11px] text-zinc-300 hover:border-zinc-400 hover:text-zinc-100"
          onclick={() => (addPickerOpen = !addPickerOpen)}>＋ Add module</button>
        {#if addPickerOpen}
          <div class="absolute right-0 z-30 mt-1 w-40 rounded-lg border border-zinc-700 bg-zinc-900 p-1 shadow-xl">
            {#each Object.entries(TYPE_LABEL) as [t, label] (t)}
              <button type="button" class="block w-full rounded px-2 py-1 text-left text-[11px] text-zinc-300 hover:bg-zinc-800"
                onclick={() => addModule(t as ModularModule['type'])}>{label}</button>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <div class="flex flex-col gap-1.5">
      {#each modules as m, i (m.id)}
        <div class="rounded-lg border border-zinc-700 bg-zinc-900/60 p-1.5">
          <div class="mb-1 flex items-center justify-between gap-1">
            <span class="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium text-zinc-300">{i + 1}. {TYPE_LABEL[m.type]}</span>
            <button type="button" class="flex h-4 w-4 items-center justify-center rounded text-zinc-500 hover:bg-zinc-800 hover:text-red-400" title="Remove module"
              onclick={() => removeModule(m.id)}>✕</button>
          </div>

          {#if m.type === 'price_move'}
            <div class="flex flex-wrap items-end gap-2">
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Move ≥ %</span>
                <input type="number" min="0.1" step="0.1" class="{sel} w-16" bind:value={m.threshold} onchange={touch} />
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Over</span>
                <select class={sel} bind:value={m.window} onchange={touch}>
                  {#each PM_WINDOWS as w (w)}<option value={w}>{w}</option>{/each}
                </select>
              </label>
            </div>
          {:else if m.type === 'positions'}
            <div class="grid grid-cols-2 gap-x-2 gap-y-1">
              <label class="col-span-2 flex flex-col gap-0.5">
                <span class={lbl}>Wallet group</span>
                <select class="{sel} w-full" value={m.groupId ?? ''} onchange={(e) => { m.groupId = e.currentTarget.value || null; touch(); }}>
                  <option value="">— select a group —</option>
                  {#each walletPinsStore.groups as g (g.id)}<option value={g.id}>{g.name}</option>{/each}
                </select>
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Rank by</span>
                <select class={sel} bind:value={m.posCriteria} onchange={touch}>
                  <option value="net_long">Net Long</option>
                  <option value="net_size">Net Size</option>
                </select>
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Staleness</span>
                <select class={sel} bind:value={m.staleness} onchange={touch}>
                  {#each PA_STALE as s (s)}<option value={s}>{s}</option>{/each}
                </select>
              </label>
            </div>
          {:else if m.type === 'positions_change'}
            <div class="grid grid-cols-2 gap-x-2 gap-y-1">
              <label class="col-span-2 flex flex-col gap-0.5">
                <span class={lbl}>Wallet group</span>
                <select class="{sel} w-full" value={m.groupId ?? ''} onchange={(e) => { m.groupId = e.currentTarget.value || null; touch(); }}>
                  <option value="">— select a group —</option>
                  {#each walletPinsStore.groups as g (g.id)}<option value={g.id}>{g.name}</option>{/each}
                </select>
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Rank by</span>
                <select class={sel} bind:value={m.pcCriteria} onchange={touch}>
                  <option value="net_pos_change">Net Pos Change</option>
                  <option value="net_open_long">Net Open Long</option>
                  <option value="net_flip">Net Flip</option>
                </select>
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>In</span>
                <select class={sel} bind:value={m.pcRankBy} onchange={touch}>
                  <option value="usd">$ amount</option>
                  <option value="wallets">Wallets</option>
                </select>
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Window</span>
                <select class={sel} bind:value={m.pcWindow} onchange={touch}>
                  {#each PC_WINDOWS as w (w)}<option value={w}>{w}</option>{/each}
                </select>
              </label>
            </div>
          {:else}
            <div class="flex flex-wrap items-end gap-2">
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Rank by</span>
                <select class={sel} bind:value={m.svCriteria} onchange={touch}>
                  <option value="spot_vd_pct">Spot VD %</option>
                  <option value="vol_pct">Vol Δ%</option>
                </select>
              </label>
              <label class="flex flex-col gap-0.5">
                <span class={lbl}>Lookback</span>
                <select class={sel} bind:value={m.svLookback} onchange={touch}>
                  {#each SV_LOOKBACKS as w (w)}<option value={w}>{w}</option>{/each}
                </select>
              </label>
            </div>
          {/if}
        </div>
      {/each}
      {#if !modules.length}
        <div class="rounded-lg border border-dashed border-zinc-700 px-2 py-3 text-center text-[11px] text-zinc-500">
          No modules yet — add one. The result is the intersection of every module.
        </div>
      {/if}
    </div>

    <!-- Columns -->
    {#if modules.length}
      <div class="mt-2 border-t border-zinc-800 pt-1.5">
        <div class="mb-1 flex items-center justify-between">
          <span class="text-[10px] font-semibold uppercase tracking-wide text-zinc-400">Columns</span>
          <span class="text-[10px] text-zinc-500">{selectedCols.length}/{MAX_COLS} · default none = tokens only</span>
        </div>
        <div class="flex flex-col gap-1">
          {#each modules as m, i (m.id)}
            <div class="flex flex-wrap items-center gap-1">
              <span class="text-[10px] text-zinc-500">{i + 1}.</span>
              {#each moduleCols(m) as c (c.id)}
                {@const on = selectedCols.includes(c.id)}
                <button type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px] {on
                    ? 'border-emerald-600 bg-emerald-900/40 text-emerald-200'
                    : colFull ? 'border-zinc-800 text-zinc-600' : 'border-zinc-700 text-zinc-300 hover:border-zinc-500'}"
                  disabled={!on && colFull}
                  onclick={() => toggleCol(c.id)}>{c.label}</button>
              {/each}
            </div>
          {/each}
        </div>
      </div>
    {/if}
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
