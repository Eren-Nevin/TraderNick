<script lang="ts">
  import { onMount } from 'svelte';
  import { flip } from 'svelte/animate';
  import { dndzone, type DndEvent } from 'svelte-dnd-action';
  import PlusCircle from '@lucide/svelte/icons/plus-circle';
  import ChartInstance from '$lib/components/ChartInstance.svelte';
  import { stopDragEvents } from '$lib/actions/stopDragEvents';
  import {
    CHART_CATEGORIES,
    CHART_KIND_LABELS,
    LIDO_L1_KINDS,
    MAX_MAS,
    chartKindCategory,
    chartKindGroup,
    chartKindGroupOrder,
    chartKindProvider,
    chartKindShortLabel,
    defaultMAs,
    newChartInstance,
    sanitizeOverlay,
    type ChartCategory,
    type ChartInstance as ChartInstanceT,
    type ChartKind,
    type ChartTemplate,
    type MAConfig
  } from '$lib/components/charts/config';
  import { sanitizeSmartSelectorState } from '$lib/components/charts/smartSelector';
  import { filtersStore } from '$lib/stores/filters.svelte';
  import { pagesStore } from '$lib/stores/pages.svelte';
  import type { ChainGroup, Interval, TokenGroup, TransferStream } from '$lib/api';
  import type { View } from '$lib/chart-zoom';

  let {
    tokens,
    streams = [],
    uniPools = [],
    lidoChains = [],
    gmxMarkets = [],
    tokenGroups = [],
    chainGroups = [],
    storageKey,
    availableKinds,
    templates = [],
    defaultLayout,
    defaultToken,
    defaultChain,
    categorizedMenu = false,
    currentPageId
  }: {
    tokens: string[];
    streams?: TransferStream[];
    uniPools?: import('$lib/api').UniswapStream[];
    lidoChains?: { event: string; chain: string; rows: number }[];
    gmxMarkets?: { event: string; chain: string; market: string; rows: number }[];
    tokenGroups?: TokenGroup[];
    chainGroups?: ChainGroup[];
    storageKey: string;
    availableKinds: ChartKind[];
    templates?: ChartTemplate[];
    defaultLayout: () => ChartInstanceT[];
    defaultToken?: string;
    defaultChain?: string;
    // When true, the Insert menu groups kinds by the 6 high-level categories
    // (Exchange / Flows / Lending / DeX / Perp / Staking) from
    // `chartKindCategory()` instead of the per-page flat+protocol-group
    // layout. Used by the cross-cutting Dashboard page.
    categorizedMenu?: boolean;
    // The pages-store id of the page this layout belongs to. Present only on
    // user Dashboard pages; the fixed Trades/Perp pages omit it. Doubles as the
    // gate for the right-click "Move to Page" menu — the menu is enabled only
    // when this is set (so it never appears on the non-page example layouts).
    currentPageId?: string;
  } = $props();

  const MAX_CHARTS = 20;
  const FLIP_MS = 250;
  // Drive the validator off CHART_KIND_LABELS so adding a new ChartKind
  // doesn't silently invalidate every saved layout that uses it. (Previous
  // hand-maintained list missed `pc` and every `aave_*` — sanitize() would
  // return null for the whole layout if any one chart was an AAVE kind.)
  const KNOWN_KINDS = Object.keys(CHART_KIND_LABELS) as ChartKind[];

  let instances = $state<ChartInstanceT[]>(defaultLayout());
  let hydrated = $state(false);

  let syncZoom = $state(false); // off by default — each chart zooms/pans independently
  let syncToken = $state(false);
  // Master "Sync Weekend lines" toggle — flipping it propagates the value
  // to every chart's instance.showWeekLines so the page-level switch is
  // an actual master override, not just a label. The flag itself isn't
  // persisted (mirrors syncToken's ephemeral behaviour); the per-instance
  // showWeekLines values it sets DO persist via the layout save effect.
  let syncWeekLines = $state(false);
  let sharedView = $state<View>(null);
  let sharedHoverTime = $state<number | null>(null);

  let insertOpen = $state(false);
  // When set, the next addChart/addTemplate/addTemplateVariant splices the
  // new chart at this index (pushing subsequent charts down). When null, the
  // chart is appended to the end. Set by the per-chart "+" hover button so
  // the menu can be reused with the right insertion target.
  let insertIdx = $state<number | null>(null);
  // When set, the next pick from the insert menu *replaces* the instance at
  // this index instead of inserting. Set by clicking a chart's title (which
  // calls openSwapAt). Mutually exclusive with insertIdx — the same menu
  // serves both modes since the catalog of pickable kinds is identical.
  let swapIdx = $state<number | null>(null);
  // IDs of templates whose parameter sub-list is currently expanded in the menu.
  let expandedTemplates = $state<Set<string>>(new Set());
  // Protocol-group names that are currently expanded in the Insert menu.
  // The Insert menu organises event-driven chart kinds (AAVE / Uniswap /
  // Lido / Aero / …) under a collapsible parent so the flat 18+ row list
  // doesn't dominate the menu. Single-family kinds (OHLCV, Token Flow,
  // Volume by Size, etc.) stay flat at the top.
  let expandedGroups = $state<Set<string>>(new Set());
  // Category-mode expansion (Dashboard page). Independent of expandedGroups
  // because the category menu has its own first-level taxonomy.
  // Top-level expansion: used for category headers in categorizedMenu mode
  // AND protocol-family headers in non-categorized mode (so the same Set
  // works for both modes, keyed by the visible header label).
  let expandedCategories = $state<Set<string>>(new Set());
  // Third-level expansion for providers with multiple versions (AAVE V2/V3/V4,
  // Uniswap V2/V3/V4, Aerodrome CL/Basic). Keyed by `${category}::${provider}`
  // so two categories could safely reuse the same provider name without colliding.
  let expandedProviders = $state<Set<string>>(new Set());

  // Insert dialog state: a single centered modal with a typeahead filter
  // and arrow-key navigation. Replaces the old click-anchored menu.
  let filterText = $state('');
  let highlightedIdx = $state(0);
  let listEl: HTMLDivElement | null = $state(null);

  function toggleGroupExpand(name: string) {
    const next = new Set(expandedGroups);
    if (next.has(name)) next.delete(name); else next.add(name);
    expandedGroups = next;
  }
  function toggleCategoryExpand(name: string) {
    const next = new Set(expandedCategories);
    if (next.has(name)) next.delete(name); else next.add(name);
    expandedCategories = next;
  }
  function toggleProviderExpand(key: string) {
    const next = new Set(expandedProviders);
    if (next.has(key)) next.delete(key); else next.add(key);
    expandedProviders = next;
  }

  function openInsertAt(idx: number, _ev: MouseEvent) {
    if (instances.length >= MAX_CHARTS) return;
    insertIdx = idx;
    swapIdx = null;
    filterText = '';
    highlightedIdx = 0;
    insertOpen = true;
  }
  /** Append-at-end variant — same as openInsertAt but leaves insertIdx
   *  null so placeInstance() falls through to the "append" branch. */
  function openInsertAtEnd(_ev: MouseEvent) {
    if (instances.length >= MAX_CHARTS) return;
    insertIdx = null;
    swapIdx = null;
    filterText = '';
    highlightedIdx = 0;
    insertOpen = true;
  }
  /** Open the menu to swap the chart at `idx` with a different kind. The
      replacement preserves width + height so the layout doesn't reflow; the
      id is fresh (a different chart = a different cache key). MAX_CHARTS does
      not gate this because we're replacing, not adding. */
  function openSwapAt(id: string, _ev: MouseEvent) {
    const idx = instances.findIndex((i) => i.id === id);
    if (idx < 0) return;
    swapIdx = idx;
    insertIdx = null;
    filterText = '';
    highlightedIdx = 0;
    insertOpen = true;
    expandedTemplates = new Set();
  }
  function closeInsert() {
    insertOpen = false;
    insertIdx = null;
    swapIdx = null;
    filterText = '';
    highlightedIdx = 0;
    // Note: expandedCategories / expandedProviders intentionally persist
    // across dialog opens so a user who always reaches for AAVE doesn't
    // have to re-expand on every insert.
  }
  function toggleTemplateExpand(id: string) {
    const next = new Set(expandedTemplates);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expandedTemplates = next;
  }
  /** Splice `inst` at insertIdx (or append if null) — OR — in swap mode,
      replace the chart at swapIdx, preserving its width + height so the
      layout grid doesn't reflow. Then close the menu. */
  function placeInstance(inst: ChartInstanceT) {
    const swap = swapIdx;
    if (swap !== null && swap >= 0 && swap < instances.length) {
      const old = instances[swap];
      const replaced: ChartInstanceT = { ...inst, width: old.width, height: old.height };
      instances = [...instances.slice(0, swap), replaced, ...instances.slice(swap + 1)];
      closeInsert();
      return;
    }
    const at = insertIdx;
    if (at === null || at < 0 || at >= instances.length) {
      instances = [...instances, inst];
    } else {
      instances = [...instances.slice(0, at), inst, ...instances.slice(at)];
    }
    closeInsert();
  }
  function addChart(kind: ChartKind) {
    // In swap mode, MAX_CHARTS doesn't apply (we're replacing, not adding).
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(newChartInstance(kind, { token: tk, chain: defaultChain }));
  }
  function addTemplate(t: ChartTemplate) {
    if (!t.build) return;
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(t.build({ token: tk, chain: defaultChain }));
  }
  function addTemplateVariant(
    build: (defaults: { token: string; chain?: string }) => ChartInstanceT
  ) {
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    placeInstance(build({ token: tk, chain: defaultChain }));
  }
  function removeChart(id: string) {
    instances = instances.filter((i) => i.id !== id);
  }

  // ---- right-click context menu ("Move to Page") ----
  // Enabled only on user Dashboard pages (currentPageId set). Anchored at the
  // cursor; the "Move to Page" row reveals a submenu of the OTHER user pages on
  // hover. Picking one writes the chart into that page's stored layout and
  // removes it from this page's live `instances` (which auto-persists).
  const MENU_W = 184;
  const SUBMENU_W = 176;
  let ctxMenu = $state<{ open: boolean; x: number; y: number; chartId: string | null }>({
    open: false,
    x: 0,
    y: 0,
    chartId: null
  });
  let ctxSubOpen = $state(false);
  // Other user pages this chart can move to (current page excluded).
  let movePages = $derived(pagesStore.pages.filter((p) => p.id !== currentPageId));

  function openCtx(e: MouseEvent, chartId: string) {
    // No-op (leave the native menu) on the non-page example layouts.
    if (!currentPageId) return;
    e.preventDefault();
    // Clamp so the menu (and its right-hand submenu) stay on-screen.
    const maxX = window.innerWidth - MENU_W - SUBMENU_W - 8;
    const maxY = window.innerHeight - 80;
    ctxMenu = {
      open: true,
      x: Math.max(8, Math.min(e.clientX, Math.max(8, maxX))),
      y: Math.max(8, Math.min(e.clientY, Math.max(8, maxY))),
      chartId
    };
    ctxSubOpen = false;
  }
  function closeCtx() {
    ctxMenu = { open: false, x: 0, y: 0, chartId: null };
    ctxSubOpen = false;
  }
  function moveChartTo(targetId: string) {
    const id = ctxMenu.chartId;
    const chart = instances.find((i) => i.id === id);
    if (chart && pagesStore.appendChartToPage(targetId, chart as unknown as Record<string, unknown>)) {
      instances = instances.filter((i) => i.id !== id);
    }
    closeCtx();
  }
  // Duplicate the right-clicked chart in place: deep-clone its config, give it
  // a fresh id (so it's a distinct widget with its own data/cache slot), and
  // insert it right after the original.
  function copyChart() {
    const id = ctxMenu.chartId;
    const idx = instances.findIndex((i) => i.id === id);
    if (idx < 0 || instances.length >= MAX_CHARTS) { closeCtx(); return; }
    const clone = structuredClone($state.snapshot(instances[idx])) as ChartInstanceT;
    clone.id =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    instances = [...instances.slice(0, idx + 1), clone, ...instances.slice(idx + 1)];
    closeCtx();
  }
  function onWindowKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && ctxMenu.open) closeCtx();
  }

  // ---- insert dialog: flat searchable item list ----
  // Each item carries a display label + a "group" breadcrumb + a lowercase
  // search key. Matching is a plain substring against the search key, so
  // typing "AA" surfaces every AAVE row, "Perp" surfaces everything in
  // the Perp category, etc.
  type FlatItem =
    | {
        type: 'kind';
        id: string;
        kind: ChartKind;
        label: string;
        group: string;
        searchKey: string;
      }
    | {
        type: 'template';
        id: string;
        build: (defaults: { token: string; chain?: string }) => ChartInstanceT;
        label: string;
        group: string;
        searchKey: string;
      };

  let flatAllItems = $derived.by((): FlatItem[] => {
    const items: FlatItem[] = [];
    // Templates first — only used on /flows currently.
    for (const t of templates) {
      if (t.build) {
        items.push({
          type: 'template',
          id: `tpl-${t.id}`,
          build: t.build,
          label: t.label,
          group: 'Templates',
          searchKey: `Templates ${t.label}`.toLowerCase()
        });
      }
      if (t.variants) {
        for (const v of t.variants) {
          items.push({
            type: 'template',
            id: `tpl-${t.id}-${v.id}`,
            build: v.build,
            label: v.label,
            group: `Templates › ${t.label}`,
            searchKey: `Templates ${t.label} ${v.label}`.toLowerCase()
          });
        }
      }
    }
    // Chart kinds: in categorizedMenu mode use the Category / Provider
    // taxonomy; otherwise use the per-page group prefix.
    for (const k of availableKinds) {
      const fullLabel = CHART_KIND_LABELS[k] ?? k;
      if (categorizedMenu) {
        const cat = chartKindCategory(k);
        const prov = chartKindProvider(k);
        const label = prov ? prov.variant : fullLabel;
        const groupParts: string[] = [];
        if (cat) groupParts.push(cat);
        if (prov) groupParts.push(prov.provider);
        const group = groupParts.join(' › ');
        items.push({
          type: 'kind',
          id: `kind-${k}`,
          kind: k,
          label,
          group,
          searchKey: `${group} ${label} ${fullLabel}`.toLowerCase()
        });
      } else {
        const grp = chartKindGroup(k);
        const label = grp ? chartKindShortLabel(k) : fullLabel;
        items.push({
          type: 'kind',
          id: `kind-${k}`,
          kind: k,
          label,
          group: grp ?? '',
          searchKey: `${grp ?? ''} ${fullLabel}`.toLowerCase()
        });
      }
    }
    return items;
  });

  // Visible rows for the dialog. Two modes:
  //   1. Filter active → flat list of matches with breadcrumbs on each row.
  //   2. No filter    → tree: category headers → optional provider sub-
  //                     header (only when ≥2 versions) → leaves. Click or
  //                     Enter on a header toggles. expandedCategories /
  //                     expandedProviders persist across opens.
  type DialogRow =
    | {
        type: 'header';
        level: 1 | 2;
        key: string;            // toggle key (top: category name; sub: `${cat}::${provider}`)
        label: string;
        expanded: boolean;
        count: number;
        scope: 'category' | 'provider';
      }
    | { type: 'leaf'; item: FlatItem; indent: 0 | 1 | 2; showGroup: boolean };

  let dialogRows = $derived.by((): DialogRow[] => {
    const q = filterText.trim().toLowerCase();

    // Filter mode: one leaf per match, with its group breadcrumb shown.
    if (q) {
      return flatAllItems
        .filter((it) => it.searchKey.includes(q))
        .map((item) => ({ type: 'leaf' as const, item, indent: 0, showGroup: true }));
    }

    // Tree mode. Bucket every item by its top-level group.
    //   categorizedMenu → top = chartKindCategory(); sub = chartKindProvider()
    //   non-categorized → top = chartKindGroup() (protocol family), no sub
    // Templates always go first if present; flat single-family kinds (no
    // group at all) bubble to the very top with no header so OHLCV / Token
    // Flow etc. don't need an extra click.
    const buckets = new Map<string, FlatItem[]>();
    const orderedTops: string[] = [];
    const FLAT = '(Flat)';
    const TEMPLATES = 'Templates';

    function push(top: string, it: FlatItem) {
      if (!buckets.has(top)) { buckets.set(top, []); orderedTops.push(top); }
      buckets.get(top)!.push(it);
    }
    for (const it of flatAllItems) {
      if (it.type === 'template') {
        push(TEMPLATES, it);
        continue;
      }
      const top = categorizedMenu
        ? (chartKindCategory(it.kind) ?? FLAT)
        : (chartKindGroup(it.kind) ?? FLAT);
      push(top, it);
    }

    // Ordering: Templates first; then categorized → CHART_CATEGORIES order;
    // non-categorized → chartKindGroupOrder. Flat-leaves bucket always
    // first inside whichever section it sits in (top of dialog).
    const finalOrder: string[] = [];
    if (buckets.has(TEMPLATES)) finalOrder.push(TEMPLATES);
    if (buckets.has(FLAT)) finalOrder.push(FLAT);
    if (categorizedMenu) {
      for (const c of CHART_CATEGORIES) {
        if (c !== TEMPLATES && c !== FLAT && buckets.has(c)) finalOrder.push(c);
      }
    } else {
      const groups = orderedTops.filter(
        (k) => k !== TEMPLATES && k !== FLAT
      );
      groups.sort((a, b) => chartKindGroupOrder(a) - chartKindGroupOrder(b));
      finalOrder.push(...groups);
    }
    // Any unexpected leftovers (defensive).
    for (const k of orderedTops) {
      if (!finalOrder.includes(k)) finalOrder.push(k);
    }

    const rows: DialogRow[] = [];
    for (const top of finalOrder) {
      const items = buckets.get(top) ?? [];

      if (top === FLAT) {
        // No header — render leaves directly at the top of the dialog.
        for (const it of items) {
          rows.push({ type: 'leaf', item: it, indent: 0, showGroup: false });
        }
        continue;
      }

      const expanded = expandedCategories.has(top);
      rows.push({
        type: 'header',
        level: 1,
        key: top,
        label: top,
        expanded,
        count: items.length,
        scope: 'category'
      });
      if (!expanded) continue;

      // Inside the category: optional provider sub-grouping (categorizedMenu only).
      if (categorizedMenu && top !== TEMPLATES) {
        // Count items per provider so we know which ones earn a sub-header.
        const provCounts = new Map<string, number>();
        for (const it of items) {
          if (it.type !== 'kind') continue;
          const p = chartKindProvider(it.kind);
          if (p) provCounts.set(p.provider, (provCounts.get(p.provider) ?? 0) + 1);
        }
        const emittedProvs = new Set<string>();
        for (const it of items) {
          let provider: string | null = null;
          if (it.type === 'kind') {
            const p = chartKindProvider(it.kind);
            if (p) provider = p.provider;
          }
          if (provider && (provCounts.get(provider) ?? 0) >= 2) {
            if (emittedProvs.has(provider)) continue;
            emittedProvs.add(provider);
            const pkey = `${top}::${provider}`;
            const pExpanded = expandedProviders.has(pkey);
            // All items for this provider, in their original order.
            const pItems = items.filter(
              (x) =>
                x.type === 'kind' && chartKindProvider(x.kind)?.provider === provider
            );
            rows.push({
              type: 'header',
              level: 2,
              key: pkey,
              label: provider,
              expanded: pExpanded,
              count: pItems.length,
              scope: 'provider'
            });
            if (pExpanded) {
              for (const pit of pItems) {
                rows.push({ type: 'leaf', item: pit, indent: 2, showGroup: false });
              }
            }
          } else {
            rows.push({ type: 'leaf', item: it, indent: 1, showGroup: false });
          }
        }
      } else {
        for (const it of items) {
          rows.push({ type: 'leaf', item: it, indent: 1, showGroup: false });
        }
      }
    }
    return rows;
  });

  // Reset the highlight whenever the filter text changes.
  $effect(() => {
    filterText;
    highlightedIdx = 0;
  });

  // Keep the highlighted row scrolled into view as the user arrow-navigates.
  $effect(() => {
    if (!listEl) return;
    highlightedIdx; // re-run on change
    const el = listEl.querySelector(`[data-idx="${highlightedIdx}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: 'nearest' });
  });

  function pickItem(item: FlatItem) {
    if (swapIdx === null && instances.length >= MAX_CHARTS) return;
    const tk = defaultToken ?? tokens[0] ?? 'BTC';
    const inst =
      item.type === 'kind'
        ? newChartInstance(item.kind, { token: tk, chain: defaultChain })
        : item.build({ token: tk, chain: defaultChain });
    placeInstance(inst);
  }

  function onSearchKey(ev: KeyboardEvent) {
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      const n = dialogRows.length;
      if (n > 0) highlightedIdx = (highlightedIdx + 1) % n;
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      const n = dialogRows.length;
      if (n > 0) highlightedIdx = (highlightedIdx - 1 + n) % n;
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      const row = dialogRows[highlightedIdx];
      if (!row) return;
      if (row.type === 'header') {
        if (row.scope === 'category') toggleCategoryExpand(row.key);
        else toggleProviderExpand(row.key);
      } else {
        pickItem(row.item);
      }
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      closeInsert();
    }
  }

  function focusSearchInput(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  // ---- drag-to-resize ----
  // CSS `gap-6` on the chart grid resolves to 1.5rem = 24px. We use this
  // to translate pointer movement into grid-step deltas.
  const GRID_GAP_PX = 24;

  // While the user is dragging a resize handle we render a floating "ghost"
  // outline that follows the pointer at pixel resolution. The chart itself
  // stays at its starting size — committing the new span only happens on
  // pointerup so the live grid doesn't reflow mid-drag (which is what
  // caused the previous snap-jitter). The ghost carries both the live
  // pixel rect and the snapped target span so we can label it.
  let resizeGhost = $state<{
    left: number;
    top: number;
    width: number;
    height: number;
    snappedW: 1 | 2 | 3 | 4;
    snappedH: 1 | 2 | 3 | 4;
  } | null>(null);

  function clamp4(n: number): 1 | 2 | 3 | 4 {
    return Math.max(1, Math.min(4, n)) as 1 | 2 | 3 | 4;
  }

  /** Start a resize drag from a wrapper-edge handle.
   *  dir = 'e' resizes width only, 's' resizes height only, 'se' both.
   *  Ghost follows the pointer continuously; the actual chart snaps to
   *  the nearest integer column/row span in [1, 4] on release. */
  function startResize(idx: number, ev: PointerEvent, dir: 'e' | 's' | 'se') {
    ev.preventDefault();
    ev.stopPropagation();
    const handle = ev.currentTarget as HTMLElement;
    const host = handle.parentElement;
    if (!host) return;
    const rect = host.getBoundingClientRect();
    const startW = instances[idx].width;
    const startH = instances[idx].height;
    const startX = ev.clientX;
    const startY = ev.clientY;
    // Pixels per single-column / single-row span step. Spans include gaps
    // between cells, so one extra column adds (colW + gap) pixels.
    const xPerStep = (rect.width + GRID_GAP_PX) / startW;
    const yPerStep = (rect.height + GRID_GAP_PX) / startH;
    // Floor / ceil widths/heights in pixels (so the ghost can't shrink to
    // nothing or balloon past the 4-span maximum).
    const minW = xPerStep - GRID_GAP_PX;            // = colW; equivalent to 1 col
    const minH = yPerStep - GRID_GAP_PX;            // = rowH; equivalent to 1 row
    const maxW = 4 * xPerStep - GRID_GAP_PX;        // 4 cols + 3 gaps
    const maxH = 4 * yPerStep - GRID_GAP_PX;

    // Seed the ghost at the current host rect.
    resizeGhost = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      snappedW: startW,
      snappedH: startH
    };

    handle.setPointerCapture(ev.pointerId);
    function onMove(e: PointerEvent) {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      let ghostW = rect.width;
      let ghostH = rect.height;
      let snapW = startW;
      let snapH = startH;
      if (dir === 'e' || dir === 'se') {
        ghostW = Math.max(minW, Math.min(maxW, rect.width + dx));
        snapW = clamp4(Math.round(startW + dx / xPerStep));
      }
      if (dir === 's' || dir === 'se') {
        ghostH = Math.max(minH, Math.min(maxH, rect.height + dy));
        snapH = clamp4(Math.round(startH + dy / yPerStep));
      }
      resizeGhost = {
        left: rect.left,
        top: rect.top,
        width: ghostW,
        height: ghostH,
        snappedW: snapW,
        snappedH: snapH
      };
    }
    function onUp(e: PointerEvent) {
      try { handle.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
      const g = resizeGhost;
      resizeGhost = null;
      if (!g) return;
      if (g.snappedW !== instances[idx].width) instances[idx].width = g.snappedW;
      if (g.snappedH !== instances[idx].height) instances[idx].height = g.snappedH;
    }
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
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
  function toggleSyncWeekLines(next: boolean) {
    // Push the new value to every chart so the toggle behaves like a
    // master override. On enable → every chart shows weekend lines; on
    // disable → every chart hides them. Per-chart toggles still work
    // afterwards but won't sync back (the master toggle is one-shot,
    // matching Sync Token).
    instances = instances.map((i) => ({ ...i, showWeekLines: next }));
    syncWeekLines = next;
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

  // Normalize a persisted exchange for the binance-spot-capable kinds
  // (ohlcv / volume / bs / sz): keep 'hl' and 'binance_spot', else 'binance'.
  function spotCapableExchange(e: unknown): 'binance' | 'hl' | 'binance_spot' {
    return e === 'hl' ? 'hl' : e === 'binance_spot' ? 'binance_spot' : 'binance';
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
      // Width / height are 1–4 spans now (resize handles let the user pick
      // any value in range). Legacy saves had width ∈ {1,2} with no height
      // — migrate those into the new range so old layouts still load.
      let width: 1 | 2 | 3 | 4 = 2;
      let height: 1 | 2 | 3 | 4 = 1;
      const rw = r.width;
      const rh = r.height;
      if (rh === 1 || rh === 2 || rh === 3 || rh === 4) {
        height = rh;
      }
      if (rw === 1 || rw === 2 || rw === 3 || rw === 4) {
        width = rw;
      }
      // Legacy: row missing entirely → reproduce the pre-resize default sizes.
      if (rh === undefined) {
        if (rw === 2) { width = 4; height = 2; }
        else { width = 2; height = 2; }
      }

      const inst: ChartInstanceT = {
        id: r.id,
        kind: r.kind,
        width,
        height,
        token: r.token,
        interval: r.interval as Interval,
        showPoint: r.showPoint !== false,
        showWeekLines: r.showWeekLines === true,
        showSum: r.showSum === true,
        mas: migrateMAs(r)
      };
      if (inst.kind === 'sz') {
        inst.under = typeof r.under === 'number' ? r.under : 10000;
        inst.over = typeof r.over === 'number' ? r.over : 100000;
        inst.underInput = typeof r.underInput === 'string' ? r.underInput : String(inst.under);
        inst.overInput = typeof r.overInput === 'string' ? r.overInput : String(inst.over);
        inst.exchange = spotCapableExchange(r.exchange);
      }
      if (inst.kind === 'bs') {
        // bs reads /trade_volume with exchange = binance | binance_spot | hl, same shape.
        inst.exchange = spotCapableExchange(r.exchange);
      }
      if (inst.kind === 'volume') {
        // Volume reads /ohlcv (same as the ohlcv chart). Supports the Binance
        // Spot dataset too.
        inst.exchange = spotCapableExchange(r.exchange);
        inst.volumeUnit = r.volumeUnit === 'usd' ? 'usd' : 'token';
      }
      if (inst.kind === 'ohlcv') {
        inst.pin = r.pin === true;
        inst.exchange = spotCapableExchange(r.exchange);
        inst.volumeUnit = r.volumeUnit === 'usd' ? 'usd' : 'token';
      }
      if (inst.kind === 'fr') {
        // Same exchange selector as ohlcv — defaults to Binance for
        // existing saved layouts. frDisplay toggles the y-axis between
        // 'rate8h' (Coinglass-style bps/8h, default) and 'apr' (annualized %).
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
        inst.frDisplay = r.frDisplay === 'apr' ? 'apr' : 'rate8h';
      }
      if (inst.kind === 'early_movers') {
        // Dual-view (table/chart) + move criteria. Restore each field from the save
        // so toolbar edits survive a reload; missing fields fall back to the
        // documented defaults (AAVE, 30d, 2%/2%, len 5, lead 1, flow, $1k).
        inst.viewMode = r.viewMode === 'chart' ? 'chart' : 'table';
        const emLb = r.emLookback;
        inst.emLookback =
          emLb === '1d' || emLb === '3d' || emLb === '7d' || emLb === '14d' || emLb === '30d' ? emLb : '30d';
        inst.emLongThr = typeof r.emLongThr === 'number' ? r.emLongThr : 2;
        inst.emShortThr = typeof r.emShortThr === 'number' ? r.emShortThr : 2;
        inst.emMaxLen = typeof r.emMaxLen === 'number' && r.emMaxLen >= 1 ? Math.floor(r.emMaxLen) : 5;
        inst.emLead = typeof r.emLead === 'number' && r.emLead >= 0 ? Math.floor(r.emLead) : 1;
        inst.emMode =
          r.emMode === 'flow' || r.emMode === 'open_flip' || r.emMode === 'position_state' ? r.emMode : 'flow';
        inst.emMinSize = typeof r.emMinSize === 'number' && r.emMinSize >= 0 ? r.emMinSize : 0;
        inst.emSkipIntra = r.emSkipIntra === true;
        const num0 = (v: unknown) => (typeof v === 'number' && v >= 0 ? v : 0);
        inst.emMinAvgSizeK = num0(r.emMinAvgSizeK);
        inst.emMinCorrectLong = num0(r.emMinCorrectLong);
        inst.emMinCorrectShort = num0(r.emMinCorrectShort);
        inst.emMinCorrectLongPct = num0(r.emMinCorrectLongPct);
        inst.emMinCorrectShortPct = num0(r.emMinCorrectShortPct);
        inst.emHideGrouped = r.emHideGrouped === true;
        inst.emMinRealizedPnlK = typeof r.emMinRealizedPnlK === 'number' ? r.emMinRealizedPnlK : null;
      }
      if (inst.kind === 'trading_pit') {
        inst.tpTokens = Array.isArray(r.tpTokens)
          ? (r.tpTokens as unknown[]).filter((t): t is string => typeof t === 'string') : ['BTC'];
        inst.tpAllTokens = r.tpAllTokens === true;
        inst.tpGroupId = typeof r.tpGroupId === 'string' ? r.tpGroupId : null;
        const lb = r.tpLookback;
        inst.tpLookback = lb === '5m' || lb === '15m' || lb === '30m' || lb === '1h' || lb === '4h' ? lb : '5m';
        inst.tpMode = r.tpMode === 'aggregate' || r.tpMode === 'overview' ? r.tpMode : 'normal';
        inst.tpFlipMode = r.tpFlipMode === 'split' ? 'split' : 'separate';
        inst.tpMinSize = typeof r.tpMinSize === 'number' && r.tpMinSize >= 0 ? r.tpMinSize : 0;
        inst.tpSide = r.tpSide === 'long' || r.tpSide === 'short' ? r.tpSide : '';
        inst.tpType = typeof r.tpType === 'string' ? r.tpType : '';
        inst.tpToken = typeof r.tpToken === 'string' ? r.tpToken : '';
        inst.tpLive = r.tpLive === true;
        inst.tpTimeFormat = r.tpTimeFormat === 'standard' ? 'standard' : 'relative';
      }
      if (inst.kind === 'group_snapshot') {
        inst.gsGroupId = typeof r.gsGroupId === 'string' ? r.gsGroupId : null;
        const gss = r.gsStaleness;
        inst.gsStaleness = gss === '1h' || gss === '4h' || gss === '1d' || gss === '3d' || gss === '14d' || gss === '30d' ? gss : '7d';
        inst.gsAsOf = r.gsAsOf === 'live' ? 'live' : 'snapshot';
        const gpl = r.gsPriceLb;
        inst.gsPriceLb = gpl === '5m' || gpl === '15m' || gpl === '4h' || gpl === '1d' ? gpl : '1h';
      }
      if (inst.kind === 'backtracker') {
        // Persist the Position-Changes dialog "Only <group>" filter (default ON):
        // an explicit untoggle is saved as false and restored here so it sticks
        // across reloads. Other bt* toolbar fields fall back to config defaults.
        if (typeof r.btGroupOnly === 'boolean') inst.btGroupOnly = r.btGroupOnly;
      }
      if (inst.kind === 'backtracker_leaderboard') {
        // Restore the widget's selections across reloads — most importantly the
        // wallet group (btGroupId), so it doesn't have to be re-picked every time.
        inst.btGroupId = typeof r.btGroupId === 'string' ? r.btGroupId : null;
        const bllb = r.blLookback;
        inst.blLookback = (['15m', '30m', '1h', '4h', '12h', '1d', '7d'] as const).includes(
          bllb as NonNullable<ChartInstanceT['blLookback']>
        ) ? (bllb as NonNullable<ChartInstanceT['blLookback']>) : '1h';
        inst.blAsOf = r.blAsOf === 'recent' ? 'recent' : 'now';
        const blps = r.blPosStaleness;
        inst.blPosStaleness = (['4h', '1d', '3d', '7d', '14d', '30d'] as const).includes(
          blps as NonNullable<ChartInstanceT['blPosStaleness']>
        ) ? (blps as NonNullable<ChartInstanceT['blPosStaleness']>) : '3d';
        inst.blPosMode = r.blPosMode === 'oi' ? 'oi' : 'consensus';
      }
      if (inst.kind === 'book_depth') {
        // Binance-only; the mode selector flips the same dataset between the
        // totals / per_level_imbalance / imbalance / stacked / *_share views.
        inst.exchange = 'binance';
        const bdModes = [
          'totals', 'per_level_imbalance', 'imbalance', 'stacked',
          'asks_share', 'bids_share', 'total_share', 'asks_bids_share'
        ];
        inst.bookDepthMode = bdModes.includes(r.bookDepthMode as string)
          ? (r.bookDepthMode as typeof inst.bookDepthMode) : 'totals';
      }
      if (inst.kind === 'oi') {
        // OI: Binance reads from binance_open_interest; HL sums per-wallet
        // size across long+short from hl_position_history. The hl-only
        // display selector picks which side(s) to render — defaults to
        // 'total' so existing saved layouts (no field) keep their look.
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
        inst.oiHlDisplay = (r.oiHlDisplay === 'long' || r.oiHlDisplay === 'short'
          || r.oiHlDisplay === 'long_short' || r.oiHlDisplay === 'long_to_short'
          || r.oiHlDisplay === 'net_pct' || r.oiHlDisplay === 'count' || r.oiHlDisplay === 'net_count')
          ? r.oiHlDisplay : 'total';
        inst.oiUnit = r.oiUnit === 'token' ? 'token' : 'usd';
      }
      if (inst.kind === 'ls') {
        // L/S: Binance is the pre-aggregated source; HL is computed live
        // from hl_position_history + hl_fills.
        inst.exchange = r.exchange === 'hl' ? 'hl' : 'binance';
      }
      if (inst.kind === 'pc') {
        // Relative Performance chart — every token vs a base. Exchange picks the
        // close-price source (binance / binance_spot / hl ohlcv table).
        inst.pcBase = typeof r.pcBase === 'string' && r.pcBase ? r.pcBase : 'BTC';
        const lb = r.pcLookback;
        inst.pcLookback = (['6h', '12h', '1d', '3d', '7d', '14d', '30d', '90d'] as const).includes(
          lb as NonNullable<ChartInstanceT['pcLookback']>
        )
          ? (lb as NonNullable<ChartInstanceT['pcLookback']>)
          : '7d';
        inst.pcTopN = r.pcTopN === 3 || r.pcTopN === 10 || r.pcTopN === 15 ? r.pcTopN : 5;
        inst.pcSide = r.pcSide === 'negative' || r.pcSide === 'all' ? r.pcSide : 'positive';
        inst.exchange = r.exchange === 'hl' ? 'hl' : r.exchange === 'binance_spot' ? 'binance_spot' : 'binance';
      }
      // AAVE chart kinds (single-event + net) need a `chain` just like the
      // transfer kind. Default to the page's preferred chain. valueMode
      // ('usd' / 'amount') is restored if previously set; otherwise default
      // to 'usd' so the chart keeps its old behaviour after migration.
      // Covers `aave_v3_*`, `aave_v2_*`, and `aave_v4_*` (every kind in
      // the union shares the `aave_v…_` prefix shape).
      if (inst.kind.startsWith('aave_v')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
      }
      // Morpho / Spark — same shape (chain + valueMode), separate prefix.
      if (inst.kind.startsWith('morpho_') || inst.kind.startsWith('spark_')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
      }
      // GMX — chain (ARB-only for now), valueMode, and gmxMarket selector.
      // Empty market string = "all markets summed".
      if (inst.kind.startsWith('gmx_')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : 'ARB';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        inst.gmxMarket = typeof r.gmxMarket === 'string' ? r.gmxMarket : 'BTC/USD [WBTC-USDC]';
      }
      // Hyperliquid — static chain='HL' chip, token from the binance roster,
      // optional wallet + wallet_category whale-tracking filters.
      if (inst.kind.startsWith('hl_')) {
        inst.chain = 'HL';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        inst.hlWallet = typeof r.hlWallet === 'string' ? r.hlWallet : '';
        inst.hlWalletCategory = typeof r.hlWalletCategory === 'string' ? r.hlWalletCategory : '';
        if (inst.kind === 'hl_top_positions') {
          inst.hlSelectedWallet = typeof r.hlSelectedWallet === 'string' ? r.hlSelectedWallet : '';
        }
        if (inst.kind === 'hl_top_vaults') {
          const sort = r.hlVaultSortBy;
          inst.hlVaultSortBy = (sort === 'deposits' || sort === 'withdrawals' || sort === 'commission')
            ? sort : 'net';
        }
        if (inst.kind === 'hl_vault_detail') {
          inst.hlSelectedVault = typeof r.hlSelectedVault === 'string' ? r.hlSelectedVault : '';
        }
        if (inst.kind === 'hl_smart_oi') {
          // Shared OI selectors (display + unit) plus saved-filter references
          // (one OI series per filter). A legacy inline `smartSelector` is
          // migrated into an auto-created saved filter so existing charts
          // keep working after the saved-filters-only switch.
          inst.exchange = 'hl';
          inst.oiHlDisplay = (r.oiHlDisplay === 'long' || r.oiHlDisplay === 'short'
            || r.oiHlDisplay === 'long_short' || r.oiHlDisplay === 'long_to_short'
            || r.oiHlDisplay === 'net' || r.oiHlDisplay === 'net_pct' || r.oiHlDisplay === 'count' || r.oiHlDisplay === 'net_count')
            ? r.oiHlDisplay : 'total';
          inst.oiUnit = r.oiUnit === 'token' ? 'token' : 'usd';
          inst.smartShowWalletCount = r.smartShowWalletCount === true;
          filtersStore.hydrate();
          if (Array.isArray(r.filterIds)) {
            inst.filterIds = (r.filterIds as unknown[]).filter(
              (x): x is string => typeof x === 'string',
            );
          } else if (r.smartSelector !== undefined) {
            const sel = sanitizeSmartSelectorState(r.smartSelector);
            inst.filterIds = [filtersStore.findOrCreateFromSelector(sel)];
          } else {
            inst.filterIds = [];
          }
        }
      }
      // Lido chart kinds need a `chain` but no token / pool. L1 kinds are
      // ETH-pinned; L2 kinds default to ARB (highest wstETH bridge volume).
      // Covers the bare 'lido' wrapper too — it carries lidoSubkind, which
      // we read to decide L1 vs L2.
      if (inst.kind === 'lido' || inst.kind.startsWith('lido_')) {
        const subkindForL1: ChartKind = inst.kind === 'lido'
          ? ((inst.lidoSubkind ?? 'lido_deposit') as ChartKind)
          : inst.kind;
        const isL1 = LIDO_L1_KINDS.has(subkindForL1);
        const ch = typeof r.chain === 'string' ? r.chain : (isL1 ? 'ETH' : (defaultChain ?? 'ARB'));
        inst.chain = isL1 ? 'ETH' : ch;
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
      }
      // Aerodrome basic-pool chart kinds (BASE-only, Solidly v1). Covers
      // the 'aero_basic' wrapper kind too.
      if (inst.kind === 'aero_basic' || inst.kind.startsWith('aero_basic_')) {
        inst.chain = 'BASE';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        const rp = r.aeroBasicPool as Record<string, unknown> | undefined;
        if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string' && typeof rp.stable === 'boolean') {
          inst.aeroBasicPool = {
            symbol0: (rp.symbol0 as string).toUpperCase(),
            symbol1: (rp.symbol1 as string).toUpperCase(),
            stable: rp.stable as boolean
          };
        } else {
          inst.aeroBasicPool = { symbol0: 'USDC', symbol1: 'WETH', stable: false };
        }
      }
      // Aerodrome CL chart kinds + the 'aero_cl' wrapper. (BASE-only.) The
      // basic branch above already matched-and-returned for aero_basic_*
      // and the 'aero_basic' wrapper, so we can rely on aero_cl_* /
      // 'aero_cl' being unambiguous here.
      else if (inst.kind === 'aero_cl' || inst.kind.startsWith('aero_cl_')) {
        inst.chain = 'BASE';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        const rp = r.aeroPool as Record<string, unknown> | undefined;
        if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string' && typeof rp.tick_spacing === 'number') {
          inst.aeroPool = {
            symbol0: (rp.symbol0 as string).toUpperCase(),
            symbol1: (rp.symbol1 as string).toUpperCase(),
            tick_spacing: rp.tick_spacing as number
          };
        } else {
          inst.aeroPool = { symbol0: 'USDC', symbol1: 'WETH', tick_spacing: 100 };
        }
      }
      // Uniswap chart kinds also need a `chain`, plus a `uniPool` 3-tuple
      // (symbol0 / symbol1 / fee). Validate the pool shape; fall back to a
      // canonical default so a corrupt save can't strand the chart.
      // valueMode supported on every uniswap_v* kind except
      // uniswap_v3_net_swap_flow (which ignores it at the chart layer).
      // V2 uses fee=0 as a sentinel for "no fee tier"; V4 carries a
      // separate uniV4Pool with extra tick_spacing + hooks fields. The
      // bare 'uniswap_v3' / 'uniswap_v2' / 'uniswap_v4' wrapper kinds
      // share the same `uniswap_v…_` prefix shape via the wrapper-kind
      // branches below — sanitize them through the same default-uniPool
      // path so a missing pool can't strand the chart after a restore.
      if (inst.kind.startsWith('uniswap_v4')) {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        const rp = r.uniV4Pool as Record<string, unknown> | undefined;
        if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string'
            && typeof rp.fee === 'number' && typeof rp.tick_spacing === 'number'
            && typeof rp.hooks === 'string') {
          inst.uniV4Pool = {
            symbol0: (rp.symbol0 as string).toUpperCase(),
            symbol1: (rp.symbol1 as string).toUpperCase(),
            fee: rp.fee as number,
            tick_spacing: rp.tick_spacing as number,
            hooks: rp.hooks as string
          };
        } else {
          inst.uniV4Pool = {
            symbol0: 'USDC', symbol1: 'WETH', fee: 500, tick_spacing: 10,
            hooks: '0x0000000000000000000000000000000000000000'
          };
        }
      } else if (inst.kind.startsWith('uniswap_v')) {
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        const rp = r.uniPool;
        if (
          rp && typeof rp === 'object' &&
          typeof (rp as Record<string, unknown>).symbol0 === 'string' &&
          typeof (rp as Record<string, unknown>).symbol1 === 'string' &&
          typeof (rp as Record<string, unknown>).fee === 'number'
        ) {
          inst.uniPool = {
            symbol0: ((rp as Record<string, unknown>).symbol0 as string).toUpperCase(),
            symbol1: ((rp as Record<string, unknown>).symbol1 as string).toUpperCase(),
            fee: (rp as Record<string, unknown>).fee as number
          };
        } else {
          inst.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 500 };
        }
      }
      if (inst.kind === 'transfer') {
        inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
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
            'sender_in', 'sender_ex', 'sender_all_in',
            'receiver_in', 'receiver_ex', 'receiver_all_in',
            'involving_in', 'involving_ex', 'involving_all_in',
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
      if (inst.kind === 'exchange_flow') {
        // Hyperliquid is ARB-only; CeXes default to ETH. Sanitize chain
        // accordingly so a stored layout that selected HL on a non-ARB
        // chain repairs itself on load.
        const ex = r.exchangeFlowExchange;
        const validEx = ['binance','coinbase','okx','bybit','hyperliquid'];
        inst.exchangeFlowExchange = validEx.includes(ex) ? ex : 'binance';
        const ft = r.exchangeFlowType;
        inst.exchangeFlowType = ft === 'inflow' || ft === 'outflow' || ft === 'all' ? ft : 'netflow';
        inst.valueMode = r.valueMode === 'amount' ? 'amount' : 'usd';
        if (inst.exchangeFlowExchange === 'hyperliquid') {
          inst.chain = 'ARB';
        } else {
          inst.chain = typeof r.chain === 'string' ? r.chain : (defaultChain ?? 'ETH');
        }
      }
      // smart_wallets_table (experimental smart-wallet finder) — restore the
      // metric / lookback / token / snapshot selectors + the configurable noise
      // guards so the chart reloads exactly as the user left it. Not an hl_
      // kind, so set the display chain chip here.
      if (inst.kind === 'smart_wallets_table') {
        inst.chain = 'HL';
        inst.swMetric = r.swMetric === 'sharpe' ? 'sharpe' : 'sharpe';
        const lb = r.swLookback;
        inst.swLookback = (lb === 1 || lb === 7 || lb === 30 || lb === 90) ? lb : 7;
        inst.swToken =
          typeof r.swToken === 'string' && r.swToken.length > 0 ? r.swToken : null;
        inst.swSnapshot = typeof r.swSnapshot === 'string' ? r.swSnapshot : undefined;
        inst.swMinDays =
          typeof r.swMinDays === 'number' && r.swMinDays >= 1 ? Math.floor(r.swMinDays) : 3;
        inst.swMinVolume =
          typeof r.swMinVolume === 'number' && r.swMinVolume >= 0 ? r.swMinVolume : 0;
        inst.swMinRealized =
          typeof r.swMinRealized === 'number' ? r.swMinRealized : 0;
        inst.swMinUnrealized = typeof r.swMinUnrealized === 'number' ? r.swMinUnrealized : null;
        inst.swMinTotalPnl = typeof r.swMinTotalPnl === 'number' ? r.swMinTotalPnl : null;
        inst.swMinOi =
          typeof r.swMinOi === 'number' && r.swMinOi >= 0 ? r.swMinOi : 0;
        inst.swMinAvgTradeSize =
          typeof r.swMinAvgTradeSize === 'number' && r.swMinAvgTradeSize >= 0 ? r.swMinAvgTradeSize : 0;
        inst.swMinTakerPct =
          typeof r.swMinTakerPct === 'number' && r.swMinTakerPct >= 0 ? r.swMinTakerPct : 0;
        inst.swMaxFeePct =
          typeof r.swMaxFeePct === 'number' ? r.swMaxFeePct : null;
        inst.swMaxFundingPct =
          typeof r.swMaxFundingPct === 'number' ? r.swMaxFundingPct : null;
        inst.swMinAccountDuration =
          typeof r.swMinAccountDuration === 'number' && r.swMinAccountDuration >= 0
            ? Math.floor(r.swMinAccountDuration) : 0;
        inst.swMinTokens =
          typeof r.swMinTokens === 'number' && r.swMinTokens >= 0 ? Math.floor(r.swMinTokens) : 0;
        inst.swMinWinRate =
          typeof r.swMinWinRate === 'number' && r.swMinWinRate >= 0 ? r.swMinWinRate : 0;
        inst.swMinTradesPerDay =
          typeof r.swMinTradesPerDay === 'number' && r.swMinTradesPerDay >= 0 ? r.swMinTradesPerDay : 0;
        inst.swMaxTradesPerDay =
          typeof r.swMaxTradesPerDay === 'number' ? r.swMaxTradesPerDay : null;
        // Dual-view: 'table' (default) or 'chart' (linked smart-OI of the found
        // wallets). Chart mode needs a token + interval + OI display/unit, which
        // the base smart_wallets_table instance otherwise lacks — default them
        // so the chart works the moment the user toggles in.
        inst.viewMode = r.viewMode === 'chart' ? 'chart' : 'table';
        inst.token =
          typeof r.token === 'string' && r.token.length > 0 ? r.token : (inst.swToken || 'BTC');
        inst.interval =
          typeof r.interval === 'string' && r.interval.length > 0 ? (r.interval as Interval) : '1h';
        inst.oiHlDisplay =
          (['total', 'long', 'short', 'long_short', 'long_to_short', 'net_pct', 'net', 'count', 'net_count'] as const)
            .includes(r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>)
            ? (r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>) : 'total';
        inst.oiUnit = r.oiUnit === 'token' ? 'token' : 'usd';
        inst.swShowClose = r.swShowClose === true;
      }
      if (inst.kind === 'smart_wallets_dynamic') {
        // Dynamic finder persists the SAME sw* criteria as the table finder, but
        // it had NO sanitize branch — so on reload every filter reverted to
        // undefined and swMinVolume fell back to 100K. Restore each field from
        // the save; missing fields fall back to the newChartInstance defaults
        // (active days 7, account age 30, win rate 60%, OI share 0.05, no
        // volume floor) so the documented defaults actually survive a reload.
        inst.chain = 'HL';
        inst.exchange = 'hl';
        inst.swMetric = 'sharpe';
        const lb = r.swLookback;
        inst.swLookback = (lb === 1 || lb === 3 || lb === 7 || lb === 14 || lb === 30) ? lb : 30;
        inst.swToken = typeof r.swToken === 'string' && r.swToken.length > 0 ? r.swToken : null;
        inst.swMinDays =
          typeof r.swMinDays === 'number' && r.swMinDays >= 1 ? Math.floor(r.swMinDays) : 7;
        inst.swMinAccountDuration =
          typeof r.swMinAccountDuration === 'number' && r.swMinAccountDuration >= 0
            ? Math.floor(r.swMinAccountDuration) : 30;
        inst.swMinWinRate =
          typeof r.swMinWinRate === 'number' && r.swMinWinRate >= 0 ? r.swMinWinRate : 60;
        inst.swMinAvgOiShare =
          typeof r.swMinAvgOiShare === 'number' && r.swMinAvgOiShare >= 0 ? r.swMinAvgOiShare : 0.05;
        inst.swMinVolume =
          typeof r.swMinVolume === 'number' && r.swMinVolume >= 0 ? r.swMinVolume : 0;
        inst.swMinRealized = typeof r.swMinRealized === 'number' ? r.swMinRealized : 0;
        inst.swMinUnrealized = typeof r.swMinUnrealized === 'number' ? r.swMinUnrealized : null;
        inst.swMinTotalPnl = typeof r.swMinTotalPnl === 'number' ? r.swMinTotalPnl : null;
        inst.swMinOi = typeof r.swMinOi === 'number' && r.swMinOi >= 0 ? r.swMinOi : 0;
        inst.swMinAvgTradeSize =
          typeof r.swMinAvgTradeSize === 'number' && r.swMinAvgTradeSize >= 0 ? r.swMinAvgTradeSize : 0;
        inst.swMinTakerPct =
          typeof r.swMinTakerPct === 'number' && r.swMinTakerPct >= 0 ? r.swMinTakerPct : 0;
        inst.swMinTokens =
          typeof r.swMinTokens === 'number' && r.swMinTokens >= 0 ? Math.floor(r.swMinTokens) : 0;
        inst.swMinTradesPerDay =
          typeof r.swMinTradesPerDay === 'number' && r.swMinTradesPerDay >= 0 ? r.swMinTradesPerDay : 0;
        inst.swMinVolumeShare =
          typeof r.swMinVolumeShare === 'number' && r.swMinVolumeShare >= 0 ? r.swMinVolumeShare : 0;
        inst.swMaxFeePct = typeof r.swMaxFeePct === 'number' ? r.swMaxFeePct : null;
        inst.swMaxFundingPct = typeof r.swMaxFundingPct === 'number' ? r.swMaxFundingPct : null;
        inst.swMaxTradesPerDay = typeof r.swMaxTradesPerDay === 'number' ? r.swMaxTradesPerDay : null;
        inst.swMinAnnualizedSharpe = typeof r.swMinAnnualizedSharpe === 'number' ? r.swMinAnnualizedSharpe : null;
        inst.swMaxAvgOiShare = typeof r.swMaxAvgOiShare === 'number' ? r.swMaxAvgOiShare : null;
        inst.swMinAvgOi = typeof r.swMinAvgOi === 'number' ? r.swMinAvgOi : null;
        inst.swMaxAvgOi = typeof r.swMaxAvgOi === 'number' ? r.swMaxAvgOi : null;
        inst.swMinAvgGlobalOi = typeof r.swMinAvgGlobalOi === 'number' ? r.swMinAvgGlobalOi : null;
        inst.swMaxAvgGlobalOi = typeof r.swMaxAvgGlobalOi === 'number' ? r.swMaxAvgGlobalOi : null;
        inst.swMinAvgGlobalOiShare = typeof r.swMinAvgGlobalOiShare === 'number' ? r.swMinAvgGlobalOiShare : null;
        inst.swMaxAvgGlobalOiShare = typeof r.swMaxAvgGlobalOiShare === 'number' ? r.swMaxAvgGlobalOiShare : null;
        inst.swMaxVolumeShare = typeof r.swMaxVolumeShare === 'number' ? r.swMaxVolumeShare : null;
        inst.viewMode =
          r.viewMode === 'table' ? 'table' : r.viewMode === 'token_list' ? 'token_list' : 'chart';
        inst.token =
          typeof r.token === 'string' && r.token.length > 0 ? r.token : (inst.swToken || 'BTC');
        inst.interval =
          typeof r.interval === 'string' && r.interval.length > 0 ? (r.interval as Interval) : '1h';
        inst.oiHlDisplay =
          (['total', 'long', 'short', 'long_short', 'long_to_short', 'net_pct', 'net', 'count', 'net_count'] as const)
            .includes(r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>)
            ? (r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>) : 'total';
        inst.oiUnit = r.oiUnit === 'token' ? 'token' : 'usd';
        inst.swtUnit = r.swtUnit === 'token' ? 'token' : 'usd';
        inst.swShowClose = r.swShowClose !== false;
        inst.smartShowWalletCount = r.smartShowWalletCount === true;
      }
      if (inst.kind === 'smart_wallets_cutoff') {
        // Static union-over-lookbacks set. Same sw* criteria as Dynamic, plus
        // the cutoff lookback multi-set, the cutoff date, and the row limit.
        inst.chain = 'HL';
        inst.exchange = 'hl';
        inst.swMetric = 'sharpe';
        inst.swToken = typeof r.swToken === 'string' && r.swToken.length > 0 ? r.swToken : null;
        const allowed = [1, 3, 7, 14, 30, 90];
        const lbs = Array.isArray(r.swCutoffLookbacks)
          ? (r.swCutoffLookbacks as unknown[])
              .map((x) => Number(x))
              .filter((x) => allowed.includes(x))
          : [];
        inst.swCutoffLookbacks = lbs.length > 0 ? Array.from(new Set(lbs)).sort((a, b) => a - b) : [...allowed];
        inst.swCutoffCombine = r.swCutoffCombine === 'intersection' ? 'intersection' : 'union';
        inst.swCutoffDate = typeof r.swCutoffDate === 'string' ? r.swCutoffDate : null;
        inst.swRowLimit = [100, 250, 500, 1000].includes(Number(r.swRowLimit)) ? Number(r.swRowLimit) : 100;
        inst.swMinDays = typeof r.swMinDays === 'number' && r.swMinDays >= 1 ? Math.floor(r.swMinDays) : 7;
        inst.swMinAccountDuration = typeof r.swMinAccountDuration === 'number' && r.swMinAccountDuration >= 0 ? Math.floor(r.swMinAccountDuration) : 30;
        inst.swMinWinRate = typeof r.swMinWinRate === 'number' && r.swMinWinRate >= 0 ? r.swMinWinRate : 60;
        inst.swMinAvgOiShare = typeof r.swMinAvgOiShare === 'number' && r.swMinAvgOiShare >= 0 ? r.swMinAvgOiShare : 0.05;
        inst.swMinVolume = typeof r.swMinVolume === 'number' && r.swMinVolume >= 0 ? r.swMinVolume : 0;
        inst.swMinRealized = typeof r.swMinRealized === 'number' ? r.swMinRealized : 0;
        inst.swMinUnrealized = typeof r.swMinUnrealized === 'number' ? r.swMinUnrealized : null;
        inst.swMinTotalPnl = typeof r.swMinTotalPnl === 'number' ? r.swMinTotalPnl : null;
        inst.swMinOi = typeof r.swMinOi === 'number' && r.swMinOi >= 0 ? r.swMinOi : 0;
        inst.swMinAvgTradeSize = typeof r.swMinAvgTradeSize === 'number' && r.swMinAvgTradeSize >= 0 ? r.swMinAvgTradeSize : 0;
        inst.swMinTakerPct = typeof r.swMinTakerPct === 'number' && r.swMinTakerPct >= 0 ? r.swMinTakerPct : 0;
        inst.swMinTokens = typeof r.swMinTokens === 'number' && r.swMinTokens >= 0 ? Math.floor(r.swMinTokens) : 0;
        inst.swMinTradesPerDay = typeof r.swMinTradesPerDay === 'number' && r.swMinTradesPerDay >= 0 ? r.swMinTradesPerDay : 0;
        inst.swMinVolumeShare = typeof r.swMinVolumeShare === 'number' && r.swMinVolumeShare >= 0 ? r.swMinVolumeShare : 0;
        inst.swMaxFeePct = typeof r.swMaxFeePct === 'number' ? r.swMaxFeePct : null;
        inst.swMaxFundingPct = typeof r.swMaxFundingPct === 'number' ? r.swMaxFundingPct : null;
        inst.swMaxTradesPerDay = typeof r.swMaxTradesPerDay === 'number' ? r.swMaxTradesPerDay : null;
        inst.swMinAnnualizedSharpe = typeof r.swMinAnnualizedSharpe === 'number' ? r.swMinAnnualizedSharpe : null;
        inst.swMaxAvgOiShare = typeof r.swMaxAvgOiShare === 'number' ? r.swMaxAvgOiShare : null;
        inst.swMinAvgOi = typeof r.swMinAvgOi === 'number' ? r.swMinAvgOi : null;
        inst.swMaxAvgOi = typeof r.swMaxAvgOi === 'number' ? r.swMaxAvgOi : null;
        inst.swMinAvgGlobalOi = typeof r.swMinAvgGlobalOi === 'number' ? r.swMinAvgGlobalOi : null;
        inst.swMaxAvgGlobalOi = typeof r.swMaxAvgGlobalOi === 'number' ? r.swMaxAvgGlobalOi : null;
        inst.swMinAvgGlobalOiShare = typeof r.swMinAvgGlobalOiShare === 'number' ? r.swMinAvgGlobalOiShare : null;
        inst.swMaxAvgGlobalOiShare = typeof r.swMaxAvgGlobalOiShare === 'number' ? r.swMaxAvgGlobalOiShare : null;
        inst.swMaxVolumeShare = typeof r.swMaxVolumeShare === 'number' ? r.swMaxVolumeShare : null;
        inst.viewMode =
          r.viewMode === 'table' ? 'table' : r.viewMode === 'token_list' ? 'token_list' : 'chart';
        inst.token =
          typeof r.token === 'string' && r.token.length > 0 ? r.token : (inst.swToken || 'BTC');
        inst.interval =
          typeof r.interval === 'string' && r.interval.length > 0 ? (r.interval as Interval) : '1h';
        inst.oiHlDisplay =
          (['total', 'long', 'short', 'long_short', 'long_to_short', 'net_pct', 'net', 'count', 'net_count'] as const)
            .includes(r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>)
            ? (r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>) : 'total';
        inst.oiUnit = r.oiUnit === 'token' ? 'token' : 'usd';
        inst.swtUnit = r.swtUnit === 'token' ? 'token' : 'usd';
        inst.swShowClose = r.swShowClose !== false;
        inst.smartShowWalletCount = false;
      }
      if (inst.kind === 'smart_wallets_group') {
        // Wallet set = a pinned group (no criteria). Restore the group id, the
        // stats lookback window, row limit, view + chart fields.
        inst.chain = 'HL';
        inst.exchange = 'hl';
        inst.swMetric = 'sharpe';
        inst.swGroupId = typeof r.swGroupId === 'string' && r.swGroupId.length > 0 ? r.swGroupId : 'default';
        const glb = r.swLookback;
        inst.swLookback = (glb === 1 || glb === 7 || glb === 30 || glb === 90 || glb === 150) ? glb : 30;
        inst.swToken = typeof r.swToken === 'string' && r.swToken.length > 0 ? r.swToken : null;
        inst.swRowLimit = [100, 250, 500, 1000].includes(Number(r.swRowLimit)) ? Number(r.swRowLimit) : 100;
        inst.viewMode =
          r.viewMode === 'chart' ? 'chart' : r.viewMode === 'token_list' ? 'token_list' : 'table';
        inst.token =
          typeof r.token === 'string' && r.token.length > 0 ? r.token : (inst.swToken || 'BTC');
        inst.interval =
          typeof r.interval === 'string' && r.interval.length > 0 ? (r.interval as Interval) : '1h';
        inst.oiHlDisplay =
          (['total', 'long', 'short', 'long_short', 'long_to_short', 'net_pct', 'net', 'count', 'net_count'] as const)
            .includes(r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>)
            ? (r.oiHlDisplay as NonNullable<ChartInstanceT['oiHlDisplay']>) : 'total';
        inst.oiUnit = r.oiUnit === 'token' ? 'token' : 'usd';
        inst.swtUnit = r.swtUnit === 'token' ? 'token' : 'usd';
        inst.swShowClose = r.swShowClose !== false;
        inst.smartShowWalletCount = false;
      }
      // Compound overlays — preserved across reloads. Each entry is validated
      // through sanitizeOverlay() so a corrupt save can't strand the chart.
      if (Array.isArray(r.overlays)) {
        const cleaned: NonNullable<ChartInstanceT['overlays']> = [];
        for (const ov of r.overlays) {
          const c = sanitizeOverlay(ov);
          if (c) cleaned.push(c);
        }
        if (cleaned.length > 0) inst.overlays = cleaned;
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
  <label class="text-xs text-zinc-400 flex items-center gap-2">
    <input
      type="checkbox"
      checked={syncWeekLines}
      onchange={(e) => toggleSyncWeekLines(e.currentTarget.checked)}
      class="accent-zinc-400"
    />
    Sync Weekend lines
  </label>
  <button type="button" onclick={resetLayout} class="text-xs text-zinc-500 hover:text-zinc-200"
    >Reset layout</button
  >
</div>

<svelte:window onkeydown={onWindowKey} />

<section
  use:dndzone={{ items: instances, flipDurationMs: FLIP_MS, dropTargetStyle: {} }}
  onconsider={handleSort}
  onfinalize={handleSort}
  class="grid grid-cols-4 gap-6"
  style="grid-auto-rows: 320px; grid-auto-flow: dense;"
>
  {#each instances as inst, idx (inst.id)}
    <div
      animate:flip={{ duration: FLIP_MS }}
      style="grid-column: span {inst.width}; grid-row: span {inst.height};"
      class="relative insert-host"
      role="presentation"
      oncontextmenu={(e) => openCtx(e, inst.id)}
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
        <PlusCircle size={16} strokeWidth={1.5} class="insert-plus-icon" />
      </button>
      <ChartInstance
        bind:instance={instances[idx]}
        {tokens}
        {streams}
        {uniPools}
        {lidoChains}
        {gmxMarkets}
        {tokenGroups}
        {chainGroups}
        {syncZoom}
        {sharedView}
        {sharedHoverTime}
        {onSharedView}
        {onSharedHover}
        {onTokenChange}
        onRemove={removeChart}
        onSwap={openSwapAt}
      />
      <!-- Resize handles (right edge, bottom edge, bottom-right corner).
           stopDragEvents prevents the underlying dnd-kit zone from
           interpreting the pointer-down as a card-reorder drag. -->
      <div
        class="resize-handle resize-e"
        onpointerdown={(e) => startResize(idx, e, 'e')}
        use:stopDragEvents
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize chart width"
      ></div>
      <div
        class="resize-handle resize-s"
        onpointerdown={(e) => startResize(idx, e, 's')}
        use:stopDragEvents
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize chart height"
      ></div>
      <div
        class="resize-handle resize-se"
        onpointerdown={(e) => startResize(idx, e, 'se')}
        use:stopDragEvents
        role="separator"
        aria-label="Resize chart"
      ></div>
    </div>
  {/each}
</section>

<!-- Live resize ghost. Position:fixed in viewport coordinates so it's
     independent of the chart grid's reflow. Carries the snapped target
     span as a small label so the user can preview where the chart will
     land before releasing. Rendered outside the section to avoid being
     clipped by the grid. -->
{#if resizeGhost}
  <div
    class="resize-ghost"
    style="left:{resizeGhost.left}px; top:{resizeGhost.top}px; width:{resizeGhost.width}px; height:{resizeGhost.height}px;"
    aria-hidden="true"
  >
    <span class="resize-ghost-label">{resizeGhost.snappedW}×{resizeGhost.snappedH}</span>
  </div>
{/if}


{#if instances.length >= MAX_CHARTS}
  <!-- At MAX_CHARTS the floating insert button and per-chart "+" hover
       zones go inert. Surface why so the user knows it's a limit, not
       a bug. -->
  <div
    class="rounded-xl border border-dashed border-amber-700/60 bg-amber-900/10 px-4 py-3 text-xs text-amber-300 flex items-center gap-2"
    role="status"
  >
    <span class="text-base leading-none">⚠</span>
    <span>
      <strong class="font-semibold">Max {MAX_CHARTS} charts reached.</strong>
      Remove a chart (its ✕ button) to insert another. This cap exists to
      keep page-level fetches in budget — each chart is its own data load.
    </span>
  </div>
{:else}
  <!-- Page-wide floating action button. Always visible (until MAX_CHARTS)
       so the user has a discoverable single-click "add a chart" target
       even on an empty layout. Anchored to the viewport — fixed
       positioning keeps it in the bottom-right corner as the user
       scrolls the chart grid. -->
  <button
    type="button"
    onclick={(e) => openInsertAtEnd(e)}
    aria-label="Insert chart"
    title="Insert chart"
    class="fab-insert"
  >
    <PlusCircle size={28} strokeWidth={1.75} />
  </button>
{/if}

<!-- Right-click context menu for a chart card. Transparent full-screen
     backdrop captures click / right-click anywhere to dismiss; the panel is
     anchored at the cursor. "Move to Page" reveals a submenu of the other user
     pages on hover. Enabled only on user Dashboard pages (currentPageId set). -->
{#if ctxMenu.open}
  <div
    class="fixed inset-0 z-50"
    onclick={closeCtx}
    oncontextmenu={(e) => { e.preventDefault(); closeCtx(); }}
    role="presentation"
  ></div>
  <div
    class="fixed z-50 min-w-[184px] py-1 bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl shadow-black/60 text-sm text-zinc-200"
    style="left: {ctxMenu.x}px; top: {ctxMenu.y}px;"
    role="menu"
    tabindex="-1"
    oncontextmenu={(e) => e.preventDefault()}
  >
    <button
      type="button"
      class="w-full text-left px-3 py-1.5 hover:bg-zinc-800 text-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed"
      role="menuitem"
      onmouseenter={() => (ctxSubOpen = false)}
      onclick={copyChart}
      disabled={instances.length >= MAX_CHARTS}
      title={instances.length >= MAX_CHARTS ? `Page is full (max ${MAX_CHARTS} charts)` : 'Duplicate this chart on this page'}
    >Duplicate</button>
    <div
      class="relative flex items-center justify-between gap-3 px-3 py-1.5 cursor-default hover:bg-zinc-800"
      role="menuitem"
      tabindex="-1"
      onmouseenter={() => (ctxSubOpen = true)}
      onmouseleave={() => (ctxSubOpen = false)}
    >
      <span>Move to Page</span>
      <span class="text-zinc-500">▸</span>
      {#if ctxSubOpen}
        <!-- Submenu floats to the right, overlapping the parent's top edge. -->
        <div
          class="absolute left-full top-0 -mt-1 ml-0.5 min-w-[176px] max-h-[60vh] overflow-y-auto py-1 bg-zinc-950 border border-zinc-700 rounded-md shadow-2xl shadow-black/60"
          role="menu"
          tabindex="-1"
        >
          {#if movePages.length === 0}
            <div class="px-3 py-1.5 text-zinc-500">No other pages</div>
          {:else}
            {#each movePages as p (p.id)}
              <button
                type="button"
                class="w-full text-left px-3 py-1.5 truncate hover:bg-zinc-800 text-zinc-200"
                role="menuitem"
                onclick={() => moveChartTo(p.id)}
                title={p.name}
              >{p.name}</button>
            {/each}
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

<!-- Centered insert dialog with typeahead filter + arrow/Enter keyboard
     navigation. Renders whenever insertOpen is true; the trigger
     (per-chart "+" or FAB) sets insertIdx / swapIdx so placeInstance
     knows where to land the chart. -->
{#if insertOpen}
  <div
    class="fixed inset-0 z-40 bg-black/55"
    onclick={closeInsert}
    role="presentation"
  ></div>
  <div
    class="fixed z-50 inset-0 flex items-start justify-center pt-24 pointer-events-none"
    role="presentation"
  >
    <div
      class="insert-dialog pointer-events-auto bg-zinc-950 border border-zinc-700 rounded-lg shadow-2xl shadow-black/60 w-[480px] max-w-[92vw] max-h-[60vh] flex flex-col overflow-hidden"
      role="dialog"
      aria-modal="true"
      aria-label="Insert chart"
      onclick={(e) => e.stopPropagation()}
    >
      {#if swapIdx !== null}
        <div class="px-3 pt-2 pb-1 text-[10px] uppercase tracking-widest text-amber-300 border-b border-zinc-800">
          Swap this chart — pick a replacement
        </div>
      {/if}
      <div class="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
        <span class="text-zinc-500 text-sm leading-none" aria-hidden="true">⌕</span>
        <input
          type="text"
          bind:value={filterText}
          onkeydown={onSearchKey}
          use:focusSearchInput
          placeholder="Search chart kinds — e.g. AA, Uniswap, OHLCV"
          class="flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
          aria-label="Search chart kinds"
        />
      </div>
      <div bind:this={listEl} class="flex-1 overflow-y-auto scrollbar-none py-1">
        {#if dialogRows.length === 0}
          <div class="px-3 py-6 text-xs text-zinc-500 text-center">No matches</div>
        {:else}
          {#each dialogRows as row, i (i)}
            {@const isHi = i === highlightedIdx}
            {#if row.type === 'header'}
              <!-- Category / provider header. Click or Enter toggles it.
                   level-1 = top category, level-2 = provider sub-group. -->
              <button
                type="button"
                data-idx={i}
                onclick={() => row.scope === 'category'
                  ? toggleCategoryExpand(row.key)
                  : toggleProviderExpand(row.key)}
                onmouseenter={() => (highlightedIdx = i)}
                aria-expanded={row.expanded}
                class="w-full flex items-center gap-2 text-left py-1.5 text-xs transition-colors
                       {row.level === 1 ? 'font-medium text-zinc-100' : 'text-zinc-200'}
                       {isHi ? 'bg-zinc-800' : 'hover:bg-zinc-900'}"
                style="padding-left: {0.75 + (row.level - 1) * 1}rem; padding-right: 0.75rem;"
              >
                <span class="text-zinc-500 text-[10px] w-3 inline-block">{row.expanded ? '▾' : '▸'}</span>
                <span class="flex-1 truncate">{row.label}</span>
                <span class="text-zinc-500 text-[10px]">{row.count}</span>
              </button>
            {:else}
              <button
                type="button"
                data-idx={i}
                onclick={() => pickItem(row.item)}
                onmouseenter={() => (highlightedIdx = i)}
                class="w-full flex items-center gap-2 text-left py-1.5 text-xs transition-colors
                       {isHi ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-300 hover:bg-zinc-900'}"
                style="padding-left: {0.75 + row.indent * 1}rem; padding-right: 0.75rem;"
              >
                {#if row.showGroup && row.item.group}
                  <span class="text-[10px] text-zinc-500 truncate">{row.item.group}</span>
                  <span class="text-[10px] text-zinc-600">›</span>
                {/if}
                <span class="truncate">{row.item.label}</span>
              </button>
            {/if}
          {/each}
        {/if}
      </div>
      <div class="px-3 py-1.5 border-t border-zinc-800 text-[10px] text-zinc-500 flex items-center justify-between">
        <span>↑↓ navigate · ↵ select / expand · esc close</span>
        <span>
          {#if filterText.trim()}
            {dialogRows.length} match{dialogRows.length === 1 ? '' : 'es'}
          {:else}
            {flatAllItems.length} item{flatAllItems.length === 1 ? '' : 's'}
          {/if}
        </span>
      </div>
    </div>
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
  /* The icon (PlusCircle from lucide) is a single SVG so a colour change
     on the stroke is all we need for the hover state. */
  :global(.insert-plus .insert-plus-icon) {
    pointer-events: none;
    color: rgb(161 161 170);                    /* zinc-400 */
    transition: color 120ms;
  }
  .insert-host > .insert-plus:hover :global(.insert-plus-icon) {
    color: rgb(96 165 250);                     /* blue-400 */
  }

  /* Resize handles. Three handles per chart: east edge (width), south
     edge (height), south-east corner (both). Each is invisible until
     the wrapper is hovered, then a subtle blue tint hints at the
     grabbable area. Hit zones are wide enough to grab without being
     pixel-perfect, but stop short of overlapping the insert-+ on the
     left and the chart's title-bar controls on top. */
  .insert-host > .resize-handle {
    position: absolute;
    z-index: 25;
    background: transparent;
    transition: background-color 120ms ease;
    touch-action: none; /* prevent the browser from scroll-snapping the drag */
  }
  .insert-host > .resize-handle.resize-e {
    top: 8px;
    bottom: 16px;
    right: -4px;
    width: 8px;
    cursor: ew-resize;
  }
  .insert-host > .resize-handle.resize-s {
    left: 8px;
    right: 16px;
    bottom: -4px;
    height: 8px;
    cursor: ns-resize;
  }
  .insert-host > .resize-handle.resize-se {
    right: -4px;
    bottom: -4px;
    width: 14px;
    height: 14px;
    cursor: nwse-resize;
  }
  /* No hover/active background — cursor change alone signals the
     grabbable edges. */

  /* Live resize ghost. Follows the pointer at pixel resolution while
     the underlying chart stays put — the chart itself only snaps to
     the new grid span on pointerup. */
  .resize-ghost {
    position: fixed;
    pointer-events: none;
    z-index: 1000;
    /* Soft desaturated outline — visible enough to track without dominating
       the chart underneath. No fill so the chart stays readable through it. */
    border: 1px dashed rgba(161 161 170 / 0.45);   /* zinc-400 / 45% */
    border-radius: 0.75rem;                         /* match chart rounded-xl */
    transition: none;
  }
  .resize-ghost-label {
    position: absolute;
    right: 6px;
    bottom: 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    color: rgb(212 212 216);                       /* zinc-300 */
    background-color: rgba(15 23 42 / 0.7);        /* slate-900/70 */
    padding: 1px 6px;
    border-radius: 4px;
  }

  /* Floating action button — fixed in the viewport's bottom-right corner.
     Single discoverable target for "add a chart" that works on empty
     layouts and replaces the old fixed dashed pad. Subtle dark fill so
     it doesn't dominate the chart grid; hover brightens + lifts. */
  .fab-insert {
    position: fixed;
    right: 1.5rem;
    bottom: 1.5rem;
    width: 3.5rem;
    height: 3.5rem;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 9999px;
    border: 1px solid rgb(63 63 70);               /* zinc-700 */
    background-color: rgb(24 24 27);               /* zinc-900 */
    color: rgb(212 212 216);                       /* zinc-300 */
    box-shadow: 0 8px 24px rgba(0 0 0 / 0.45);
    cursor: pointer;
    transition: transform 120ms ease, background-color 120ms ease,
                color 120ms ease, border-color 120ms ease;
  }
  .fab-insert:hover {
    background-color: rgb(39 39 42);               /* zinc-800 */
    border-color: rgb(82 82 91);                   /* zinc-600 */
    color: rgb(244 244 245);                       /* zinc-100 */
    transform: translateY(-1px);
  }
  .fab-insert:active {
    transform: translateY(0);
  }
  .fab-insert:focus-visible {
    outline: 2px solid rgb(96 165 250);            /* blue-400 */
    outline-offset: 2px;
  }
</style>
