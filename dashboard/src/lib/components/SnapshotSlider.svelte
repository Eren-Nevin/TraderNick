<script lang="ts">
  // Shared snapshot-day picker: a 1-day-grain slider whose move is DEFERRED —
  // dragging only updates a pending position; nothing commits (and so nothing
  // re-queries) until the user clicks OK. Used by both the Smart Wallets table
  // header and the dual-view chart toolbar so picking a snapshot in either view
  // is byte-identical — same range, same grain, same applied ISO day.
  import { DAY_SLIDER_MAX_BACK, isoToBack, backToIso } from '$lib/daySlider';
  import { untrack } from 'svelte';

  let {
    snapshot,
    onChangeSnapshot,
    label = 'Snapshot',
    hint = ''
  }: {
    snapshot: string; // resolved ISO date YYYY-MM-DD (the APPLIED day)
    onChangeSnapshot: (iso: string) => void;
    label?: string; // leading caption (e.g. "End date")
    hint?: string; // muted suffix after the date (e.g. the resolved window)
  } = $props();

  const MAX_BACK = DAY_SLIDER_MAX_BACK;
  // Local sliderPos is the source of truth (bind:value). The sync effect only
  // re-runs on `snapshot` changes (sliderPos is untracked), so a pending drag is
  // never fought by a re-asserted value.
  let sliderPos = $state(MAX_BACK);
  $effect(() => {
    const want = MAX_BACK - isoToBack(snapshot);
    if (untrack(() => sliderPos) !== want) sliderPos = want;
  });
  const pendingSnapshot = $derived(backToIso(MAX_BACK - sliderPos));
  const pendingDirty = $derived(pendingSnapshot !== snapshot);
  function commitPos() {
    onChangeSnapshot(backToIso(MAX_BACK - sliderPos));
  }
</script>

<div class="flex items-center gap-3 w-full text-xs">
  <span class="text-zinc-500 whitespace-nowrap">{label}:</span>
  <span
    class={'font-mono whitespace-nowrap ' + (pendingDirty ? 'text-amber-300' : 'text-zinc-200')}
    title={pendingDirty ? 'Pending — click OK to apply' : undefined}
  >{pendingSnapshot}</span>
  {#if hint}
    <span class="text-[10px] text-zinc-500 whitespace-nowrap">{hint}</span>
  {/if}
  <input
    type="range"
    min="0"
    max={MAX_BACK}
    step="1"
    bind:value={sliderPos}
    class="flex-1 accent-blue-500 cursor-pointer"
    title="Drag to pick a snapshot day, then click OK (1-day grain)"
  />
  <button
    type="button"
    onclick={commitPos}
    disabled={!pendingDirty}
    class={'text-[11px] px-2 py-0.5 rounded border whitespace-nowrap ' + (pendingDirty
      ? 'border-blue-500 bg-blue-600/30 text-blue-200 hover:bg-blue-600/50'
      : 'border-zinc-700 bg-zinc-900 text-zinc-600 cursor-default')}
    title="Apply the selected snapshot day"
  >OK</button>
  <button
    type="button"
    onclick={() => { sliderPos = MAX_BACK; commitPos(); }}
    class="text-[10px] text-zinc-500 hover:text-zinc-200 underline decoration-dotted whitespace-nowrap"
    title="Jump to the most recent day"
  >Today</button>
</div>
