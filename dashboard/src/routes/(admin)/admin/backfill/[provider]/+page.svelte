<script lang="ts">
  // Backfill/{provider}: jobs table filtered to this provider's job_types,
  // then one BackfillForm per matching form spec stacked vertically.
  //
  // Providers with multiple forms (Binance, Transfers) show every form
  // each with its own since/until/force inputs + Kick button, so each
  // can be fired independently.

  import { page } from '$app/state';
  import { getContext } from 'svelte';
  import { ADMIN_CTX_KEY, type AdminContext } from '$lib/admin/types';
  import {
    formsForProvider,
    jobProvider,
    providerFromSlug,
  } from '$lib/admin/providers';
  import BackfillJobsTable from '$lib/admin/components/BackfillJobsTable.svelte';
  import BackfillForm from '$lib/admin/components/BackfillForm.svelte';
  import FillBoardSection from '$lib/admin/components/FillBoardSection.svelte';

  const ctx = getContext<AdminContext>(ADMIN_CTX_KEY);

  let provider = $derived(providerFromSlug(page.params.provider ?? ''));
  let jobsFiltered = $derived(
    provider ? ctx.jobs.filter((j) => jobProvider(j.job_type) === provider) : [],
  );
  let forms = $derived(provider ? formsForProvider(provider) : []);
  // Scope the table's Clear button to this provider's job_types. The
  // form spec keys are unprefixed (e.g. `binance_ohlcv`); the backend
  // matches `job_type` which is prefixed (`backfill_binance_ohlcv`).
  let clearScopeJobTypes = $derived(forms.map((f) => `backfill_${f.type}`));
</script>

<div class="px-8 py-6 space-y-6">
  {#if !provider}
    <div class="text-sm text-red-300">Unknown provider slug: {page.params.provider}</div>
  {:else}
    <div>
      <h1 class="text-xl font-semibold">{provider} backfill</h1>
      <div class="text-xs text-zinc-500">
        {forms.length} backfill form{forms.length === 1 ? '' : 's'}
        · {jobsFiltered.length} job{jobsFiltered.length === 1 ? '' : 's'} in history
      </div>
    </div>

    {#if ctx.jobsErr}
      <div class="text-xs text-red-300 bg-red-950/30 p-2 rounded">{ctx.jobsErr}</div>
    {/if}
    <BackfillJobsTable
      jobs={jobsFiltered}
      cancelJob={ctx.cancelJob}
      clearFinished={ctx.clearFinishedJobs}
      {clearScopeJobTypes}
    />

    <FillBoardSection {provider} />

    <section class="space-y-4">
      <h2 class="text-sm font-semibold text-zinc-300 uppercase tracking-wide">Kick new backfill</h2>
      {#each forms as f (f.type)}
        <BackfillForm form={f} onSubmitted={() => ctx.refresh()} />
      {/each}
    </section>
  {/if}
</div>
