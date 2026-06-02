<script lang="ts">
  // LiveStreams/{provider}: one StreamsGroupTable for the provider that
  // matches the URL slug. State comes from the (admin) layout context.

  import { page } from '$app/state';
  import { getContext } from 'svelte';
  import { ADMIN_CTX_KEY, type AdminContext } from '$lib/admin/types';
  import { providerFromSlug } from '$lib/admin/providers';
  import StreamsGroupTable from '$lib/admin/components/StreamsGroupTable.svelte';

  const ctx = getContext<AdminContext>(ADMIN_CTX_KEY);

  let provider = $derived(providerFromSlug(page.params.provider ?? ''));
  let rows = $derived(ctx.streams.filter((s) => s.group === provider));
</script>

<div class="px-8 py-6 space-y-4">
  {#if !provider}
    <div class="text-sm text-red-300">Unknown provider slug: {page.params.provider}</div>
  {:else}
    <div>
      <h1 class="text-xl font-semibold">{provider}</h1>
      <div class="text-xs text-zinc-500">
        Live streams for this provider · {rows.length} streams
      </div>
    </div>
    {#if ctx.streamsErr}
      <div class="text-xs text-red-300 bg-red-950/30 p-2 rounded">{ctx.streamsErr}</div>
    {/if}
    <StreamsGroupTable
      groupName={provider}
      {rows}
      streamAction={ctx.streamAction}
      hideHeader={true}
    />
  {/if}
</div>
