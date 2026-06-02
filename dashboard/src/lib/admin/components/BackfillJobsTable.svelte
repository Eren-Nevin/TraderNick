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
          <tr class="border-t border-zinc-800 hover:bg-zinc-900/40">
            <td class="px-2 py-1 font-mono" title={j.job_id}>{j.job_id.slice(0, 12)}…</td>
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
</section>
