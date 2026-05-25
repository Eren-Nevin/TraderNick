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
    type ChartTemplate,
    type MAConfig
  } from '$lib/components/charts/config';
  import type { ChainGroup, Interval, TokenGroup, TransferStream } from '$lib/api';
  import type { View } from '$lib/chart-zoom';

  let {
    tokens,
    streams = [],
    tokenGroups = [],
    chainGroups = [],
    storageKey,
    availableKinds,
    templates = [],
    defaultLayout,
    defaultToken,
    defaultChain
  }: {
    tokens: string[];
    streams?: TransferStream[];
    tokenGroups?: TokenGroup[];
    chainGroups?: ChainGroup[];
    storageKey: string;
    availableKinds: ChartKind[];
    templates?: ChartTemplate[];
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
  // When set, the next addChart/addTemplate/addTemplateVariant splices the
  // new chart at this index (pushing subsequent charts down). When null, the
  // chart is appended to the end. Set by the per-chart "+" hover button so
  // the menu can be reused with the right insertion target.
  let insertIdx = $state<number | null>(null);
  // Viewport coords of the "+" that triggered the menu, used so the menu can
  // appear next to the click instead of always at the bottom pad. null when
  // the menu was triggered from the bottom "+ Insert Chart" pad.
  let insertMenuPos = $state<{ x: number; y: number } | null>(null);
  // IDs of templates whose parameter sub-list is currently expanded in the menu.
  let expandedTemplates = $state<Set<string>>(new Set());

  function openInsert() {
    if (instances.length >= MAX_CHARTS) return;
    insertOpen = !insertOpen;
    insertIdx = null;
    insertMenuPos = null;
    if (!insertOpen) expandedTemplates = new Set();
  }
  function openInsertAt(idx: number, ev: MouseEvent) {
    if (instances.length >= MAX_CHARTS) return;
    insertIdx = idx;
    // Anchor the menu to the clicked +. getBoundingClientRect would be more
    // precise; using clientX/Y is fine since the menu uses translate-X to
    // centre itself on the anchor.
    insertMenuPos = { x: ev.clientX, y: ev.clientY };
    insertOpen = true;
    expandedTemplates = new Set();
  }
  function closeInsert() {
    insertOpen = false;
    insertIdx = null;
    insertMenuPos = null;
    expandedTemplates = new Set();
  }
  function toggleTemplateExpand(id: string) {
    const next = new Set(expandedTemplates);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expandedTemplates = next;
  }
  /** Splice `inst` at insertIdx (or append if null), then close the menu. */
  function placeInstance(inst: ChartInstanceT) {
    const at = insertIdx;
    if (at === null || at < 0 || at >= instances.length) {
      instances = [...instances, inst];
    } else {
      instances = [...instances.slice(0, at), inst, ...instances.slice(at)];
    }
    closeInsert();
  }
  function addChart(kind: ChartKind) {
    if (instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(newChartInstance(kind, { token: tk, chain: defaultChain }));
  }
  function addTemplate(t: ChartTemplate) {
    if (instances.length >= MAX_CHARTS || !t.build) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(t.build({ token: tk, chain: defaultChain }));
  }
  function addTemplateVariant(
    build: (defaults: { token: string; chain?: string }) => ChartInstanceT
  ) {
    if (instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(build({ token: tk, chain: defaultChain }));
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
      if (inst.kind === 'pc') {
        // Price Comparison chart — the overlay token list is the *whole*
        // configuration alongside instance.token.
        inst.overlayTokens = Array.isArray(r.overlayTokens)
          ? r.overlayTokens
              .map((t) => (typeof t === 'string' ? t : ''))
              .filter((t) => t.length > 0)
              .slice(0, 5)
          : [];
      }
      if (inst.kind === 'transfer') {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        // Migration: the previous compound-token registry had a "Native" entry
        // that was a virtual cross-chain bundle. It's been removed; the native
        // assets (ETH on ETH/ARB/BASE, BNB on BSC, POL on POLYGON) are being
        // ingested as real streams instead. Old layouts referencing it would
        // 404 the aggregate, so reset to the page default.
        if (inst.token === 'Native') {
          inst.token = defaultToken ?? tokens[0] ?? 'BTC';
        }
        // New shape: a single `filter` field. Migrate from the older
        // `extraSeries[0].filters` if present (we keep only the first; the
        // rest are dropped now that the chart shows only one series).
        function pickFilter(src: unknown): Record<string, string[]> {
          const out: Record<string, string[]> = {};
          if (!src || typeof src !== 'object') return out;
          const rf = src as Record<string, unknown>;
          for (const k of [
            'sender_in', 'sender_ex',
            'receiver_in', 'receiver_ex',
            'involving_in', 'involving_ex',
            'sender_entity_in', 'sender_entity_ex',
            'receiver_entity_in', 'receiver_entity_ex',
            'involving_entity_in', 'involving_entity_ex',
            'sender_addr_in', 'sender_addr_ex',
            'receiver_addr_in', 'receiver_addr_ex',
            'involving_addr_in', 'involving_addr_ex'
          ]) {
            const v = rf[k];
            if (Array.isArray(v)) {
              const cleaned = v
                .map((x) => (typeof x === 'string' ? x : ''))
                .filter((x) => x.length > 0);
              if (cleaned.length) out[k] = cleaned;
            }
          }
          return out;
        }
        let filter: Record<string, string[]> = pickFilter(r.filter);
        if (Object.keys(filter).length === 0 && Array.isArray(r.extraSeries) && r.extraSeries.length > 0) {
          const first = r.extraSeries[0] as Record<string, unknown> | undefined;
          if (first && typeof first === 'object') {
            filter = pickFilter(first.filters);
          }
        }
        inst.filter = filter;
        // Netflow templates persist two locked filter sets (positive − negative).
        if (r.netFilter && typeof r.netFilter === 'object') {
          const nf = r.netFilter as Record<string, unknown>;
          const positive = pickFilter(nf.positive);
          const negative = pickFilter(nf.negative);
          if (
            Object.keys(positive).length > 0 ||
            Object.keys(negative).length > 0
          ) {
            inst.netFilter = { positive, negative };
          }
        }
        if (typeof r.templateName === 'string' && r.templateName.length > 0) {
          inst.templateName = r.templateName;
        }
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
      class="relative insert-host"
    >
      <!-- "+" hover zone sitting in the column-gap to the left of this chart.
           Clicking opens the insert menu pre-set to insert *before* this chart. -->
      <button
        type="button"
        class="insert-plus"
        aria-label="Insert chart before this one"
        title="Insert chart here"
        onclick={(e) => openInsertAt(idx, e)}
      >
        <span class="insert-plus-dot">+</span>
      </button>
      <ChartInstance
        bind:instance={instances[idx]}
        {tokens}
        {streams}
        {tokenGroups}
        {chainGroups}
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

{#snippet insertMenuBody()}
  {#if templates.length > 0}
    <div class="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-widest text-zinc-500">
      Templates
    </div>
    {#each templates as t (t.id)}
      {#if t.variants && t.variants.length > 0}
        <button
          type="button"
          onclick={() => toggleTemplateExpand(t.id)}
          class="flex items-center justify-between w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
          aria-expanded={expandedTemplates.has(t.id)}
        >
          <span>{t.label}</span>
          <span class="text-zinc-500 text-[10px] ml-2"
            >{expandedTemplates.has(t.id) ? '▾' : '▸'}</span
          >
        </button>
        {#if expandedTemplates.has(t.id)}
          <div class="bg-zinc-900/40">
            {#each t.variants as v (v.id)}
              <button
                type="button"
                onclick={() => addTemplateVariant(v.build)}
                class="block w-full text-left pl-7 pr-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
              >{v.label}</button>
            {/each}
          </div>
        {/if}
      {:else if t.build}
        <button
          type="button"
          onclick={() => addTemplate(t)}
          class="block w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800"
        >{t.label}</button>
      {/if}
    {/each}
    <div class="border-t border-zinc-800 my-1"></div>
  {/if}
  <div class="px-3 pt-0.5 pb-0.5 text-[10px] uppercase tracking-widest text-zinc-500">
    Blank chart
  </div>
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
{/snippet}

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
      {#if insertOpen && insertMenuPos === null}
        <div
          class="absolute z-30 top-12 left-1/2 -translate-x-1/2 bg-zinc-950 border border-zinc-700 rounded-md shadow-xl shadow-black/60 py-1 min-w-[260px] max-h-[60vh] overflow-y-auto"
          role="menu"
        >
          {@render insertMenuBody()}
        </div>
      {/if}
    </div>
  </div>
{/if}

<!-- Floating insert menu — anchored to the per-chart "+" that opened it. -->
{#if insertOpen && insertMenuPos !== null}
  <!-- Click-outside scrim. Captures clicks anywhere on the page and closes the menu. -->
  <div
    class="fixed inset-0 z-40"
    onclick={closeInsert}
    role="presentation"
  ></div>
  <div
    class="fixed z-50 bg-zinc-950 border border-zinc-700 rounded-md shadow-xl shadow-black/60 py-1 min-w-[260px] max-h-[60vh] overflow-y-auto"
    style="left: {Math.min(Math.max(insertMenuPos.x - 130, 8), (typeof window !== 'undefined' ? window.innerWidth : 1200) - 268)}px; top: {insertMenuPos.y + 8}px;"
    role="menu"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => { if (e.key === 'Escape') closeInsert(); }}
  >
    {@render insertMenuBody()}
  </div>
{/if}

<style>
  /* Insert-between-charts affordance. Each chart wrapper hosts an absolute
     button overhanging the column gap to the LEFT of it. The button is
     invisible until the wrapper is hovered, at which point a small "+"
     circle appears centred along the left edge.

     The hit area is taller than the visible circle (24px wide × full chart
     height) so a casual hover near the left of the chart triggers it. The
     circle uses pointer-events: none so the click target is the whole bar,
     not just the dot. */
  .insert-host > .insert-plus {
    position: absolute;
    /* The grid gap between items is 1.5rem (gap-6 = 24px). Span the full
       width of that gap so the "+" is centred in the empty space, not
       overlapping the chart card. For first-column wrappers (no gap to
       the left, only page margin), the button still renders cleanly in
       that whitespace. */
    left: -1.5rem;
    top: 0;
    bottom: 0;
    width: 1.5rem;
    z-index: 20;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 120ms ease;
    background: transparent;
    border: none;
    cursor: pointer;
  }
  /* Show when the wrapper is hovered, or when the button itself is
     focus-visible (keyboard access). */
  .insert-host:hover > .insert-plus,
  .insert-host > .insert-plus:focus-visible {
    opacity: 1;
  }
  .insert-plus-dot {
    pointer-events: none;
    width: 24px;
    height: 24px;
    border-radius: 9999px;
    background-color: rgb(24 24 27);            /* zinc-900 */
    border: 1px solid rgb(82 82 91);            /* zinc-600 */
    color: rgb(228 228 231);                    /* zinc-200 */
    font-size: 16px;
    line-height: 22px;
    text-align: center;
    display: inline-block;
    transition: background-color 120ms, border-color 120ms;
  }
  .insert-host > .insert-plus:hover .insert-plus-dot {
    background-color: rgb(59 130 246 / 0.25);   /* blue-500/25 */
    border-color: rgb(96 165 250);              /* blue-400 */
    color: rgb(219 234 254);                    /* blue-100 */
  }
</style>
