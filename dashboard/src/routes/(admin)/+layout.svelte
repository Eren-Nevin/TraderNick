<script lang="ts">
  import '../../app.css';
  // Admin's own root layout — sits in a SvelteKit route group `(admin)/`
  // so the main dashboard's +layout.svelte (with its Sidebar) doesn't
  // apply here. The dashboard chrome is in the `(app)/` group instead.
  //
  // This layout owns the 1s polling loop + the streams[]/jobs[] state and
  // exposes them via setContext('admin'). Every admin page reads through
  // getContext, so navigating Overview <-> LiveStreams/* <-> Backfill/*
  // shares one fetch loop and one source of truth.

  import { onDestroy, onMount, setContext } from 'svelte';
  import AdminSidebar from '$lib/admin/components/AdminSidebar.svelte';
  import {
    ADMIN_CTX_KEY,
    type AdminContext,
    type JobRow,
    type StreamAction,
    type StreamRow,
  } from '$lib/admin/types';

  let streams = $state<StreamRow[]>([]);
  let jobs = $state<JobRow[]>([]);
  let streamsErr = $state<string | null>(null);
  let jobsErr = $state<string | null>(null);
  let lastRefresh = $state<number | null>(null);

  async function refresh() {
    try {
      const res = await fetch('/api/admin/streams');
      if (!res.ok) throw new Error(`streams ${res.status}`);
      const body = await res.json();
      streams = body.streams ?? [];
      streamsErr = null;
    } catch (e) {
      streamsErr = String(e);
    }
    try {
      const res = await fetch('/api/admin/jobs?limit=100');
      if (!res.ok) throw new Error(`jobs ${res.status}`);
      const body = await res.json();
      jobs = (Array.isArray(body) ? body : body.jobs) ?? [];
      jobsErr = null;
    } catch (e) {
      jobsErr = String(e);
    }
    lastRefresh = Date.now();
  }

  async function streamAction(name: string, action: StreamAction) {
    const res = await fetch(`/api/admin/streams/${encodeURIComponent(name)}/${action}`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`${action} ${name} → ${res.status} ${await res.text()}`);
    await refresh();
  }

  async function cancelJob(id: string) {
    const res = await fetch(`/api/admin/jobs/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`cancel ${id} → ${res.status}`);
    await refresh();
  }

  async function clearFinishedJobs(jobTypes?: string[]): Promise<{ deleted: number }> {
    const res = await fetch('/api/admin/jobs/clear-finished', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(jobTypes && jobTypes.length ? { job_types: jobTypes } : {})
    });
    if (!res.ok) throw new Error(`clear-finished → ${res.status} ${await res.text()}`);
    const body = await res.json();
    await refresh();
    return { deleted: Number(body?.deleted ?? 0) };
  }

  // Pass a getter-based proxy so consumers see the latest $state value
  // every time they read these fields (instead of a snapshot taken at
  // setContext time).
  const ctx: AdminContext = {
    get streams() { return streams; },
    get jobs() { return jobs; },
    get streamsErr() { return streamsErr; },
    get jobsErr() { return jobsErr; },
    get lastRefresh() { return lastRefresh; },
    refresh,
    streamAction,
    cancelJob,
    clearFinishedJobs,
  };
  setContext(ADMIN_CTX_KEY, ctx);

  let refreshTimer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    refresh();
    refreshTimer = setInterval(refresh, 1000);
  });
  onDestroy(() => {
    if (refreshTimer) clearInterval(refreshTimer);
  });

  let { children } = $props();
</script>

<svelte:head>
  <title>Admin — TraderNick</title>
</svelte:head>

<div class="flex h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100">
  <AdminSidebar />
  <main class="flex-1 min-w-0 overflow-auto">
    {@render children()}
  </main>
</div>
