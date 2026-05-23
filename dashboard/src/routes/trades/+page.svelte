<script lang="ts">
  import { onMount } from 'svelte';
  import { flip } from 'svelte/animate';
  import { dndzone, type DndEvent } from 'svelte-dnd-action';
  import ChartInstance from '$lib/components/ChartInstance.svelte';
  import {
    CHART_KIND_LABELS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { Interval } from '$lib/api';
  import type { PageData } from './$types';
  import type { View } from '$lib/chart-zoom';

  let { data }: { data: PageData } = $props();

  const STORAGE_KEY = 'tradernick:trades:layout:v1';
  const MAX_CHARTS = 10;
  const CHART_KINDS: ChartKind[] = ['ohlcv', 'oi', 'fr', 'bs', 'sz', 'tt', 'ls'];

  function defaultLayout(): ChartInstanceT[] {
    const tk = (data.tokens && data.tokens[0]) || 'BTC';
    const mk = (k: ChartKind) => {
      const inst = newChartInstance(k, { token: tk });
      inst.interval = (data.interval as Interval) ?? '1h';
      return inst;
    };
    return [mk('ohlcv'), mk('oi'), mk('fr'), mk('bs'), mk('sz'), mk('tt'), mk('ls')];
  }

  // Hydration: start with default; replace with saved layout if any.
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
    const tk = (data.tokens && data.tokens[0]) || 'BTC';
    const inst = newChartInstance(kind, { token: tk });
    instances = [...instances, inst];
    insertOpen = false;
  }

  function removeChart(id: string) {
    instances = instances.filter((i) => i.id !== id);
  }

  // ---- drag-drop via svelte-dnd-action ----
  const FLIP_MS = 250;

  function handleSort(e: CustomEvent<DndEvent<ChartInstanceT>>) {
    instances = e.detail.items as ChartInstanceT[];
  }

  // ---- sync zoom dispatch (consumed by ChartInstance via props) ----
  function onSharedView(v: View) {
    sharedView = v;
  }
  function onSharedHover(t: number | null) {
    sharedHoverTime = t;
  }
  function toggleSync(next: boolean) {
    // Note: when flipping ON, sharedView keeps its current value (possibly null).
    // When flipping OFF, individual charts continue with whatever local view they had.
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
    return typeof s === 'string' && (CHART_KINDS as string[]).includes(s);
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
      const inst: ChartInstanceT = {
        id: r.id,
        kind: r.kind,
        width: r.width === 2 ? 2 : 1,
        token: r.token,
        interval: r.interval as Interval,
        showPoint: r.showPoint !== false,
        showCumulative: r.showCumulative === true,
        maLength: typeof r.maLength === 'number' ? r.maLength : 9,
        maType:
          r.maType === 'ema' || r.maType === 'wma' || r.maType === 'sma'
            ? r.maType
            : 'sma'
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
      out.push(inst);
      if (out.length >= MAX_CHARTS) break;
    }
    return out;
  }

  onMount(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        const restored = sanitize(parsed?.charts);
        if (restored && restored.length > 0) instances = restored;
      }
    } catch {
      // ignore — fall back to default
    }
    hydrated = true;
  });

  $effect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, charts: instances }));
    } catch {
      // localStorage may be full or disabled; ignore
    }
  });

  function resetLayout() {
    if (!confirm('Reset chart layout to defaults?')) return;
    instances = defaultLayout();
  }
</script>

<div class="p-6 space-y-6">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Trades</h1>
      <div class="text-xs text-zinc-500">Binance OHLCV + raw trades via DeFiStream</div>
    </div>
    <div class="flex items-end gap-3 flex-wrap">
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
      <button
        type="button"
        onclick={resetLayout}
        class="text-xs text-zinc-500 hover:text-zinc-200"
      >Reset layout</button>
    </div>
  </div>

  <section
    use:dndzone={{ items: instances, flipDurationMs: FLIP_MS, dropTargetStyle: {} }}
    onconsider={handleSort}
    onfinalize={handleSort}
    class="grid grid-cols-1 md:grid-cols-2 gap-6"
    style="grid-auto-flow: dense;"
  >
    {#each instances as inst, idx (inst.id)}
      <div
        animate:flip={{ duration: FLIP_MS }}
        style="grid-column: span {inst.width}"
      >
        <ChartInstance
          bind:instance={instances[idx]}
          tokens={data.tokens}
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
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div
        class="relative rounded border border-dashed border-zinc-700 bg-zinc-950/30 min-h-[180px] flex items-center justify-center"
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
            class="absolute z-30 top-12 left-1/2 -translate-x-1/2 bg-zinc-950 border border-zinc-700 rounded shadow-xl shadow-black/60 py-1 min-w-[160px]"
            role="menu"
          >
            {#each CHART_KINDS as k (k)}
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

  <div class="text-[11px] text-zinc-500">
    Drag a panel header to reorder. Click the ⇔ button to expand/shrink between 1 and 2 columns.
    Layout is saved per-browser in localStorage. Max {MAX_CHARTS} charts.
  </div>
</div>
