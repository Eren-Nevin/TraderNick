<script lang="ts">
  import '../../app.css';
  import Sidebar from '$lib/components/Sidebar.svelte';
  let { children } = $props();
</script>

<div class="flex h-full">
  <Sidebar />
  <main class="flex-1 min-w-0 overflow-auto">{@render children()}</main>
</div>
