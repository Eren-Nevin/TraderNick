<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    title = '',
    collapsed = $bindable(false),
    controls,
    children
  }: {
    title?: string;
    collapsed?: boolean;
    controls?: Snippet;
    children?: Snippet;
  } = $props();
</script>

<div class="rounded border border-zinc-800 bg-zinc-950 overflow-hidden flex flex-col">
  <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-zinc-900">
    <button
      type="button"
      onclick={() => (collapsed = !collapsed)}
      class="flex items-center gap-2 text-zinc-400 hover:text-zinc-100 select-none"
    >
      <span class="text-[10px] w-3 inline-block text-center leading-none">
        {collapsed ? '▶' : '▼'}
      </span>
      <span class="text-[10px] uppercase tracking-widest">{title}</span>
    </button>
    {#if controls}
      <div class="flex items-center gap-3 flex-wrap">{@render controls()}</div>
    {/if}
  </div>
  {#if !collapsed}
    {@render children?.()}
  {/if}
</div>
