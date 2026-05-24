<script lang="ts">
  import { onMount } from 'svelte';
  import { flip } from 'svelte/animate';
  import { dndzone, type DndEvent } from 'svelte-dnd-action';
  import ChartInstance from '$lib/components/ChartInstance.svelte';
  import {
    CHART_KIND_LABELS,
    MAX_MAS,
    defaultMAs,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind,
    type MAConfig
  } from '$lib/components/charts/config';
  import type { Interval, TransferStream } from '$lib/api';
  import type { View } from '$lib/chart-zoom';

  let {
    tokens,
    streams = [],
    storageKey,
    availableKinds,
    defaultLayout,
    defaultToken,
    defaultChain
  }: {
    tokens: string[];
    streams?: TransferStream[];
    storageKey: string;
    availableKinds: ChartKind[];
    defaultLayout: () => ChartInstanceT[];
    defaultToken?: string;
    defaultChain?: string;
  } = $props();

  const MAX_CHARTS = 10;
  const FLIP_MS = 250;
  const KNOWN_KINDS: ChartKind[] = ['ohlcv', 'oi', 'fr', 'bs', 'sz', 'tt', 'ls', 'transfer'];

  let instances = $state<ChartInstanceT[]>(defaultLayout());
  let hydrated = $state(false);

  let syncZoom = $state(true);
  let syncToken = $state(false);
  let sharedView = $state<View>(null);
  let sharedHoverTime = $state<number | null>(null);

  let insertOpen = $state(false);

  function openInsert() {
    if (instances.length >= MAX_CHARTS) return;
    insertOpen = !insertOpen;
  }
  function closeInsert() {
    insertOpen = false;
  }
  function addChart(kind: ChartKind) {
    if (instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    const inst = newChartInstance(kind, { token: tk, chain: defaultChain });
    instances = [...instances, inst];
    insertOpen = false;
  }
  function removeChart(id: string) {
    instances = instances.filter((i) => i.id !== id);
  }

  // ---- drag-drop via svelte-dnd-action ----
  function handleSort(e: CustomEvent<DndEvent<ChartInstanceT>>) {
    instances = e.detail.items as ChartInstanceT[];
  }

  // ---- sync zoom + token ----
  function onSharedView(v: View) {
    sharedView = v;
  }
  function onSharedHover(t: number | null) {
    sharedHoverTime = t;
  }
  function toggleSync(next: boolean) {
    syncZoom = next;
  }
  function toggleSyncToken(next: boolean) {
    if (next && instances.length > 0) {
      const t = instances[0].token;
      instances = instances.map((i) => ({ ...i, token: t }));
    }
    syncToken = next;
  }
  function onTokenChange(id: string, newToken: string) {
    if (syncToken) {
      instances = instances.map((i) => ({ ...i, token: newToken }));
    } else {
      const idx = instances.findIndex((i) => i.id === id);
      if (idx >= 0) instances[idx].token = newToken;
    }
  }

  // ---- persistence ----
  function isChartKind(s: unknown): s is ChartKind {
    return typeof s === 'string' && (KNOWN_KINDS as string[]).includes(s);
  }
  function migrateMAs(r: Record<string, unknown>): MAConfig[] {
    const fresh = defaultMAs();
    if (Array.isArray(r.mas)) {
      const out: MAConfig[] = [];
      for (const m of r.mas) {
        if (!m || typeof m !== 'object') continue;
        const mm = m as Record<string, unknown>;
        out.push({
          enabled: mm.enabled === true,
          length: typeof mm.length === 'number' ? mm.length : 9,
          type:
            mm.type === 'ema' || mm.type === 'wma' || mm.type === 'sma'
              ? (mm.type as MAConfig['type'])
              : 'sma'
        });
        if (out.length >= MAX_MAS) break;
      }
      while (out.length < MAX_MAS) out.push(fresh[out.length]);
      return out;
    }
    // Legacy single-MA fields.
    const length = typeof r.maLength === 'number' ? r.maLength : 9;
    const type =
      r.maType === 'ema' || r.maType === 'wma' || r.maType === 'sma'
        ? (r.maType as MAConfig['type'])
        : 'sma';
    const enabled = r.showCumulative === true;
    fresh[0] = { enabled, length, type };
    return fresh;
  }

  function sanitize(arr: unknown): ChartInstanceT[] | null {
    if (!Array.isArray(arr)) return null;
    const out: ChartInstanceT[] = [];
    for (const raw of arr) {
      if (!raw || typeof raw !== 'object') return null;
      const r = raw as Record<string, unknown>;
      if (typeof r.id !== 'string') return null;
      if (!isChartKind(r.kind)) return null;
      if (typeof r.token !== 'string') return null;
      if (typeof r.interval !== 'string') return null;
      // Size migration: the old format had width ∈ {1,2} and no height. Map
      // old → new so existing saved layouts keep their look.
      //   old width=1 → new 2×2 (default)
      //   old width=2 → new 4×2 (wide)
      let width: 1 | 2 | 4;
      let height: 1 | 2;
      if (r.height === 1 || r.height === 2) {
        width = r.width === 1 || r.width === 2 || r.width === 4 ? r.width : 2;
        height = r.height;
      } else if (r.width === 2) {
        width = 4;
        height = 2;
      } else {
        width = 2;
        height = 2;
      }

      const inst: ChartInstanceT = {
        id: r.id,
        kind: r.kind,
        width,
        height,
        token: r.token,
        interval: r.interval as Interval,
        showPoint: r.showPoint !== false,
        mas: migrateMAs(r)
      };
      if (inst.kind === 'sz') {
        inst.under = typeof r.under === 'number' ? r.under : 10000;
        inst.over = typeof r.over === 'number' ? r.over : 100000;
        inst.underInput = typeof r.underInput === 'string' ? r.underInput : String(inst.under);
        inst.overInput = typeof r.overInput === 'string' ? r.overInput : String(inst.over);
      }
      if (inst.kind === 'ohlcv') {
        inst.pin = r.pin === true;
      }
      if (inst.kind === 'transfer') {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        const rawExtras = Array.isArray(r.extraSeries) ? r.extraSeries : [];
        const cleanExtras: { id: string; name: string; filters: Record<string, string[]> }[] = [];
        for (const e of rawExtras) {
          if (!e || typeof e !== 'object') continue;
          const er = e as Record<string, unknown>;
          if (typeof er.id !== 'string') continue;
          const filters: Record<string, string[]> = {};
          const rf = er.filters;
          if (rf && typeof rf === 'object') {
            for (const k of ['sender_in', 'sender_ex', 'receiver_in', 'receiver_ex', 'involving_in', 'involving_ex']) {
              const v = (rf as Record<string, unknown>)[k];
              if (Array.isArray(v)) {
                const cleaned = v
                  .map((x) => (typeof x === 'string' ? x : ''))
                  .filter((x) => x.length > 0);
                if (cleaned.length) filters[k] = cleaned;
              }
            }
          }
          if (Object.keys(filters).length === 0) continue;
          cleanExtras.push({
            id: er.id,
            name: typeof er.name === 'string' ? er.name : '',
            filters
          });
          if (cleanExtras.length >= 3) break;
        }
        inst.extraSeries = cleanExtras;
      }
      out.push(inst);
      if (out.length >= MAX_CHARTS) break;
    }
    return out;
  }

  onMount(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        const restored = sanitize(parsed?.charts);
        if (restored && restored.length > 0) instances = restored;
      }
    } catch {
      // fall back to default
    }
    hydrated = true;
  });

  $effect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: 1, charts: instances }));
    } catch {
      // localStorage may be full or disabled
    }
  });

  function resetLayout() {
    if (!confirm('Reset chart layout to defaults?')) return;
    instances = defaultLayout();
  }
</script>

<div class="flex items-end justify-end gap-3 flex-wrap">
  <label class="text-xs text-zinc-400 flex items-center gap-2">
    <input
      type="checkbox"
      checked={syncZoom}
      onchange={(e) => toggleSync(e.currentTarget.checked)}
      class="accent-zinc-400"
    />
    Sync zoom
  </label>
  <label class="text-xs text-zinc-400 flex items-center gap-2">
    <input
      type="checkbox"
      checked={syncToken}
      onchange={(e) => toggleSyncToken(e.currentTarget.checked)}
      class="accent-zinc-400"
    />
    Sync Token
  </label>
  <button type="button" onclick={resetLayout} class="text-xs text-zinc-500 hover:text-zinc-200"
    >Reset layout</button
  >
</div>

<section
  use:dndzone={{ items: instances, flipDurationMs: FLIP_MS, dropTargetStyle: {} }}
  onconsider={handleSort}
  onfinalize={handleSort}
  class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6"
  style="grid-auto-rows: 320px; grid-auto-flow: dense;"
>
  {#each instances as inst, idx (inst.id)}
    <div
      animate:flip={{ duration: FLIP_MS }}
      style="grid-column: span {inst.width}; grid-row: span {inst.height};"
    >
      <ChartInstance
        bind:instance={instances[idx]}
        {tokens}
        {streams}
        {syncZoom}
        {sharedView}
        {sharedHoverTime}
        {onSharedView}
        {onSharedHover}
        {onTokenChange}
        onRemove={removeChart}
      />
    </div>
  {/each}
</section>

{#if instances.length < MAX_CHARTS}
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
    <div
      class="relative rounded-xl border border-dashed border-zinc-700 bg-zinc-950/30 min-h-[180px] flex items-center justify-center"
      role="region"
      aria-label="Insert chart"
    >
      <button
        type="button"
        onclick={openInsert}
        class="text-sm text-zinc-400 hover:text-zinc-100 px-3 py-2"
      >+ Insert Chart</button>
      {#if insertOpen}
        <div
          class="absolute z-30 top-12 left-1/2 -translate-x-1/2 bg-zinc-950 border border-zinc-700 rounded-md shadow-xl shadow-black/60 py-1 min-w-[180px]"
          role="menu"
        >
          {#each availableKinds as k (k)}
            <button
              type="button"
              onclick={() => addChart(k)}
              class="block w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
            >{CHART_KIND_LABELS[k]}</button>
          {/each}
          <div class="border-t border-zinc-800 mt-1 pt-1">
            <button
              type="button"
              onclick={closeInsert}
              class="block w-full text-left px-3 py-1 text-[10px] uppercase tracking-widest text-zinc-500 hover:text-zinc-300"
            >Cancel</button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}
