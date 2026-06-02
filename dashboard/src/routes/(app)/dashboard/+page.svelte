<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Cross-cutting picker: every wrapper kind from every per-category page.
  // The Insert menu groups these by high-level category (Exchange / Flows /
  // Lending / DeX / Perp / Staking) via DynamicChartLayout's categorizedMenu
  // mode. Sub-events (Deposits / Withdrawals / Net …) live inside each
  // wrapper's in-chart event picker, so they don't appear here as separate
  // entries.
  const AVAILABLE_KINDS: ChartKind[] = [
    // Exchange (CEX OHLCV + derivatives — works for both Binance and HL via
    // the in-chart exchange selector).
    'ohlcv', 'pc', 'oi', 'fr', 'bs', 'sz', 'tt', 'ls',
    // Flows.
    'transfer', 'exchange_flow',
    // Lending.
    'aave_v3', 'aave_v2', 'aave_v4', 'morpho', 'spark',
    // DeX.
    'uniswap_v3', 'uniswap_v2', 'uniswap_v4', 'aero_cl', 'aero_basic',
    // Perp.
    'gmx_v2',
    'hl_pnl', 'hl_unrealized_pnl', 'hl_transfers', 'hl_vault_net',
    'hl_top_vaults', 'hl_top_vault_lps', 'hl_vault_detail',
    'hl_top_traders', 'hl_top_positions',
    // Staking.
    'lido'
  ];

  function defaultLayout(): ChartInstanceT[] {
    // Blank canvas — user builds their own dashboard. Existing per-category
    // pages still ship with their opinionated default layouts.
    return [];
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Dashboard</h1>
      <div class="text-xs text-zinc-500">
        Mix any chart from any category — Exchange · Flows · Lending · DeX · Perp · Staking.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    streams={data.streams}
    uniPools={data.uniPools}
    lidoChains={data.lidoChains}
    gmxMarkets={data.gmxMarkets}
    tokenGroups={data.tokenGroups}
    chainGroups={data.chainGroups}
    storageKey="tradernick:dashboard:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    categorizedMenu
    defaultChain="ETH"
    {defaultLayout}
  />
</div>
