<script lang="ts">
  import DynamicChartLayout from '$lib/components/DynamicChartLayout.svelte';
  import {
    HL_CHART_KINDS,
    newChartInstance,
    type ChartInstance as ChartInstanceT,
    type ChartKind
  } from '$lib/components/charts/config';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Single Perp page hosts every perp wrapper: GMX V2 (positions / liq /
  // swaps / LP via in-chart event selector) plus the Hyperliquid family
  // (OHLCV, bs/sz, funding, PnL, leaderboards, transfers, vault flow).
  // bs / sz / ohlcv / fr support both venues via the exchange selector.
  const AVAILABLE_KINDS: ChartKind[] = ['ohlcv', 'volume', 'bs', 'sz', 'gmx_v2', ...HL_CHART_KINDS];

  const GMX_DEFAULT_MARKET = 'BTC/USD [WBTC-USDC]';

  function defaultLayout(): ChartInstanceT[] {
    const out: ChartInstanceT[] = [];

    const gmx = newChartInstance('gmx_v2', { token: 'USDC', chain: 'ARB' });
    gmx.gmxMarket = GMX_DEFAULT_MARKET;
    gmx.interval = '4h';
    out.push(gmx);

    const HL_KINDS: ChartKind[] = [
      'ohlcv',
      'bs',
      'sz',
      'fr',
      'hl_pnl',
      'hl_unrealized_pnl',
      'hl_smart_oi',
      'hl_top_traders',
      'hl_top_positions',
      'hl_transfers',
      'hl_vault_net'
    ];
    for (const kind of HL_KINDS) {
      const inst = newChartInstance(kind, { token: 'BTC', chain: 'HL' });
      inst.interval = '4h';
      if (kind === 'ohlcv' || kind === 'fr' || kind === 'bs' || kind === 'sz') inst.exchange = 'hl';
      out.push(inst);
    }
    return out;
  }
</script>

<div class="px-12 py-6 space-y-10">
  <div class="flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-xl font-semibold">Perp</h1>
      <div class="text-xs text-zinc-500">
        GMX V2 (ARB) · Hyperliquid — per-protocol event series in one place.
      </div>
    </div>
  </div>

  <DynamicChartLayout
    tokens={data.tokens}
    gmxMarkets={data.gmxMarkets}
    storageKey="tradernick:perp:layout:v1"
    availableKinds={AVAILABLE_KINDS}
    {defaultLayout}
  />
</div>
