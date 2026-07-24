<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import { pagesStore, pageLayoutKey } from '$lib/stores/pages.svelte';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Every wrapper kind across every per-category page. The Insert menu
  // groups by high-level category (Exchange / Flows / Lending / DeX /
  // Perp / Staking) via DynamicChartLayout's categorizedMenu mode.
  const AVAILABLE_KINDS: ChartKind[] = [
    'ohlcv', 'pc', 'oi', 'volume', 'fr', 'book_depth', 'bs', 'sz', 'tt', 'ls', 'ps', 'realized_price', 'spot_cvd',
    'spot_cvd_table', 'token_leaderboard',
    'transfer', 'exchange_flow',
    'aave_v3', 'aave_v2', 'aave_v4', 'morpho', 'spark',
    'aave_v3_top_wallets', 'aave_v2_top_wallets', 'aave_v4_top_wallets',
    'uniswap_v3', 'uniswap_v2', 'uniswap_v4', 'aero_cl', 'aero_basic',
    'uniswap_v3_top_wallets', 'uniswap_v2_top_wallets', 'uniswap_v4_top_wallets',
    'gmx_v2',
    'hl_pnl', 'hl_unrealized_pnl', 'hl_smart_oi', 'hl_transfers', 'hl_vault_net',
    'hl_top_vaults', 'hl_top_vault_lps', 'hl_vault_detail',
    'hl_top_traders', 'hl_top_positions',
    'smart_wallets_table', 'smart_wallets_dynamic', 'smart_wallets_cutoff', 'smart_wallets_group',
    'backtracker', 'backtracker_leaderboard', 'early_movers', 'trading_pit', 'group_snapshot',
    'lido',
    'notification'
  ];

  function defaultLayout(): ChartInstanceT[] {
    // Blank canvas — user builds their own page from the categorized
    // Insert menu. The static Example pages (Trades, Flows, Lending, …)
    // still ship with their opinionated defaults.
    return [];
  }

  // Hydrate the pages store on first mount so the title resolves to the
  // user-set name instead of falling back to the id.
  onMount(() => pagesStore.hydrate());

  // Derived per route param: title and per-page storage key. DynamicChartLayout
  // is keyed by storageKey so swapping pages re-mounts it with the right layout.
  let pageId = $derived($page.params.pageId);
  let title = $derived(
    pagesStore.pages.find((p) => p.id === pageId)?.name ?? 'Dashboard'
  );
  let storageKey = $derived(pageLayoutKey(pageId));
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">{title}</h1>
      <div class="text-xs text-zinc-500">
        Mix any chart from any category — Exchange · Flows · Lending · DeX · Perp · Staking.
      </div>
    </div>
  </div>

  {#key pageId}
    <DynamicChartLayout
      tokens={data.tokens}
      streams={data.streams}
      uniPools={data.uniPools}
      lidoChains={data.lidoChains}
      gmxMarkets={data.gmxMarkets}
      tokenGroups={data.tokenGroups}
      chainGroups={data.chainGroups}
      {storageKey}
      currentPageId={pageId}
      availableKinds={AVAILABLE_KINDS}
      categorizedMenu
      defaultChain="ETH"
      {defaultLayout}
    />
  {/key}
</div>
