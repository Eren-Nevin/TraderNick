<script lang="ts">
  import FillBoard from './FillBoard.svelte';
  import { calendarEventsForProvider } from '$lib/admin/calendar_events';
  import type { Provider } from '$lib/admin/providers';

  type Props = { provider: Provider };
  let { provider }: Props = $props();

  // One fill board per event under this provider. Spark = 6 boards,
  // Binance = 6, HL = 8, AAVE V3 = 6, etc. Each board fires its own
  // fetch; the grid layout caps width at ~3 columns on wide screens
  // so the page stays scannable.
  let events = $derived(calendarEventsForProvider(provider));
</script>

<section class="space-y-3">
  <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">
    Coverage ({events.length} event{events.length === 1 ? '' : 's'})
  </h2>
  <div class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
    {#each events as ev (ev.event_key)}
      <FillBoard eventKey={ev.event_key} label={ev.label} chains={ev.chains} />
    {/each}
  </div>
</section>
