<script lang="ts">
  // Backfill jobs table — running + recent history. Optionally filtered by
  // the parent page (e.g. only show jobs for one provider's job_types).

  import { ageMs, fmtAge } from '$lib/admin/fmt';
  import type { JobRow } from '$lib/admin/types';

  type Props = {
    jobs: JobRow[];
    cancelJob: (id: string) => Promise<void>;
  };
  let { jobs, cancelJob }: Props = $props();

  let nRunning = $derived(jobs.filter((j) => j.status === 'running').length);

  // Click-to-inspect: a single selected job ID drives the details panel
  // below the table. Click the same ID again (or any other UI that drops
  // it from the list) to close.
  let selectedId = $state<string | null>(null);
  let selectedJob = $derived(jobs.find((j) => j.job_id === selectedId) ?? null);

  function toggleSelect(id: string) {
    selectedId = selectedId === id ? null : id;
  }

  // Args we show specially up top (window). Everything else (tokens,
  // events, chains, pairs, force, custom fields …) goes into the
  // "user-supplied parameters" rendering. completed_chunks is the
  // job's internal checkpointing — useful but very noisy, so we
  // surface a count instead of dumping the full list.
  const TIME_KEYS = ['since', 'until'];
  function nonTimeArgs(args: Record<string, unknown> | undefined | null): [string, unknown][] {
    if (!args) return [];
    return Object.entries(args).filter(([k]) => !TIME_KEYS.includes(k));
  }
  function fmtArgValue(k: string, v: unknown): string {
    if (k === 'completed_chunks' && Array.isArray(v)) return `${v.length} chunks done`;
    if (Array.isArray(v)) return v.length <= 30 ? v.join(', ') : `${v.slice(0, 30).join(', ')} … (+${v.length - 30})`;
    if (typeof v === 'object' && v !== null) return JSON.stringify(v);
    return String(v);
  }
</script>

<section class="space-y-2">
  <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">
    Backfill jobs ({nRunning})
    <span class="text-[10px] text-zinc-500 font-normal normal-case">running · {jobs.length} total in window</span>
  </h2>
  <div class="overflow-auto border border-zinc-800 rounded-md">
    <table class="text-xs w-full">
      <thead class="bg-zinc-900 text-zinc-400">
        <tr>
          <th class="px-2 py-1.5 text-left">Job</th>
          <th class="px-2 py-1.5 text-left">Type</th>
          <th class="px-2 py-1.5 text-left">Status</th>
          <th class="px-2 py-1.5 text-right">%</th>
          <th class="px-2 py-1.5 text-left">Window</th>
          <th class="px-2 py-1.5 text-left">Args</th>
          <th class="px-2 py-1.5 text-left">Started</th>
          <th class="px-2 py-1.5 text-left">Updated</th>
          <th class="px-2 py-1.5 text-left">Error</th>
          <th class="px-2 py-1.5 text-right">Action</th>
        </tr>
      </thead>
      <tbody>
        {#each jobs as j (j.job_id)}
          <tr class="border-t border-zinc-800 hover:bg-zinc-900/40 {selectedId === j.job_id ? 'bg-zinc-900/70' : ''}">
            <td class="px-2 py-1">
              <button
                type="button"
                class="font-mono text-zinc-200 hover:text-emerald-300 underline-offset-2 hover:underline"
                title={selectedId === j.job_id ? 'Click to close details' : 'Click to view submitted args'}
                onclick={() => toggleSelect(j.job_id)}
              >{j.job_id.slice(0, 12)}…</button>
            </td>
            <td class="px-2 py-1 text-zinc-400">{j.job_type.replace(/^backfill_/, '')}</td>
            <td class="px-2 py-1">
              {#if j.status === 'running'}<span class="text-emerald-400">{j.status}</span>
              {:else if j.status === 'failed'}<span class="text-red-400">{j.status}</span>
              {:else if j.status === 'cancelled'}<span class="text-amber-400">{j.status}</span>
              {:else if j.status === 'completed'}<span class="text-zinc-400">{j.status}</span>
              {:else}<span class="text-zinc-500">{j.status}</span>
              {/if}
            </td>
            <td class="px-2 py-1 text-right tabular-nums">{(j.progress * 100).toFixed(0)}</td>
            <td class="px-2 py-1 text-zinc-400 whitespace-nowrap" title={`${j.args?.since ?? ''} → ${j.args?.until ?? ''}`}>
              {((j.args?.since as string | undefined) ?? '?').slice(0, 10)} → {((j.args?.until as string | undefined) ?? '?').slice(0, 10)}
            </td>
            <td class="px-2 py-1 text-zinc-500 max-w-xs truncate font-mono" title={JSON.stringify(j.args ?? {})}>
              {Object.entries(j.args ?? {})
                .filter(([k]) => !['since', 'until', 'force', 'completed_chunks'].includes(k))
                .map(([k, v]) => `${k}=${Array.isArray(v) ? v.length : v}`)
                .join(' ')}
            </td>
            <td class="px-2 py-1 text-zinc-500 whitespace-nowrap">{(j.started_at ?? '').slice(11, 19)}</td>
            <td class="px-2 py-1 text-zinc-500 whitespace-nowrap">{fmtAge(ageMs(j.updated_at))}</td>
            <td class="px-2 py-1 text-red-300 max-w-xs truncate" title={j.error ?? ''}>{j.error ?? ''}</td>
            <td class="px-2 py-1 text-right whitespace-nowrap">
              {#if j.status === 'running' || j.status === 'pending'}
                <button
                  class="text-xs px-2 py-0.5 bg-zinc-900 border border-zinc-700 rounded hover:border-amber-500 hover:text-amber-300"
                  onclick={() => cancelJob(j.job_id)}
                >cancel</button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if selectedJob}
    <!-- Details panel for the clicked job. Shows everything that came in
         from the submission body (tokens, events, chains, pairs, force …)
         so the user can verify the job was started with the right inputs.
         Internal bookkeeping (completed_chunks) is summarised to a count. -->
    <section class="border border-zinc-700 bg-zinc-900/40 rounded-md p-3 text-xs space-y-2">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-zinc-500 text-[10px] uppercase tracking-widest">Job details</span>
          <span class="font-mono text-zinc-200">{selectedJob.job_id}</span>
          <span class="text-zinc-500">·</span>
          <span class="text-zinc-300">{selectedJob.job_type}</span>
        </div>
        <button
          type="button"
          class="text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5"
          onclick={() => (selectedId = null)}
          aria-label="Close details"
        >✕</button>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-zinc-400">
        <div><span class="text-zinc-500">Status:</span> <span class="text-zinc-200">{selectedJob.status}</span></div>
        <div><span class="text-zinc-500">Progress:</span> <span class="text-zinc-200 tabular-nums">{(selectedJob.progress * 100).toFixed(1)}%</span></div>
        <div><span class="text-zinc-500">Started:</span> <span class="text-zinc-200">{selectedJob.started_at ?? '—'}</span></div>
        <div><span class="text-zinc-500">Updated:</span> <span class="text-zinc-200">{selectedJob.updated_at ?? '—'}</span></div>
        <div><span class="text-zinc-500">Finished:</span> <span class="text-zinc-200">{selectedJob.finished_at ?? '—'}</span></div>
        <div><span class="text-zinc-500">Since:</span> <span class="text-zinc-200">{(selectedJob.args?.since as string | undefined) ?? '—'}</span></div>
        <div><span class="text-zinc-500">Until:</span> <span class="text-zinc-200">{(selectedJob.args?.until as string | undefined) ?? '—'}</span></div>
      </div>
      <div>
        <div class="text-zinc-500 text-[10px] uppercase tracking-widest mb-1">Submitted parameters</div>
        <table class="text-xs w-full font-mono">
          <tbody>
            {#each nonTimeArgs(selectedJob.args) as [k, v] (k)}
              <tr class="border-t border-zinc-800">
                <td class="px-2 py-1 text-zinc-500 align-top w-48">{k}</td>
                <td class="px-2 py-1 text-zinc-200 whitespace-pre-wrap break-all">{fmtArgValue(k, v)}</td>
              </tr>
            {/each}
            {#if nonTimeArgs(selectedJob.args).length === 0}
              <tr class="border-t border-zinc-800">
                <td colspan="2" class="px-2 py-1 text-zinc-500 italic">(no extra parameters — used job-type defaults)</td>
              </tr>
            {/if}
          </tbody>
        </table>
      </div>
      {#if selectedJob.error}
        <div>
          <div class="text-red-400 text-[10px] uppercase tracking-widest mb-1">Error</div>
          <pre class="text-red-300 whitespace-pre-wrap break-all">{selectedJob.error}</pre>
        </div>
      {/if}
    </section>
  {/if}
</section>
