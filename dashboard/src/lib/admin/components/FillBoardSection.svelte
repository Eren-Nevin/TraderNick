<script lang="ts">
  import FillBoard from './FillBoard.svelte';
  import { calendarEventsForProvider } from '$lib/admin/calendar_events';
  import type { Provider } from '$lib/admin/providers';

  type Props = { provider: Provider };
  let { provider }: Props = $props();

  // One fill board per event under this provider. Spark = 6 boards,
  // Binance = 6, HL = 8, AAVE V3 = 6, etc. Each board fires its OWN
  // /gaps/calendar query, so a provider fans out 6-8 heavy ClickHouse
  // queries. We therefore DON'T load them on mount — the user clicks
  // "Load coverage" (then "Refresh" to refetch). `nonce` bumps to force a
  // remount of the boards so Refresh actually re-runs the queries (each
  // FillBoard fetches in onMount).
  let events = $derived(calendarEventsForProvider(provider));

  let shown = $state(false);
  let nonce = $state(0);

  // This component instance is reused across the [provider] route (only the
  // prop changes on navigation), so reset back to the un-loaded state when
  // the provider changes — otherwise opening the next provider page would
  // auto-fire its heavy queries, which is exactly what we're avoiding.
  $effect(() => {
    provider; // track
    shown = false;
  });

  function loadOrRefresh() {
    shown = true;
    nonce += 1;
  }
</script>

<section class="space-y-3">
  <div class="flex items-center justify-between gap-2">
    <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">
      Coverage ({events.length} event{events.length === 1 ? '' : 's'})
    </h2>
    <button
      type="button"
      onclick={loadOrRefresh}
      class="text-xs px-3 py-1 rounded border border-zinc-700 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
    >
      {shown ? 'Refresh' : 'Load coverage'}
    </button>
  </div>

  {#if shown}
    {#key nonce}
      <div class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
        {#each events as ev (ev.event_key)}
          <FillBoard eventKey={ev.event_key} label={ev.label} chains={ev.chains} />
        {/each}
      </div>
    {/key}
  {:else}
    <div
      class="text-xs text-zinc-500 border border-dashed border-zinc-800 rounded-md p-4 text-center"
    >
      Coverage runs {events.length} heavy {events.length === 1 ? 'query' : 'queries'}
      against ClickHouse. Click <span class="text-zinc-300">Load coverage</span> to fetch.
    </div>
  {/if}
</section>
