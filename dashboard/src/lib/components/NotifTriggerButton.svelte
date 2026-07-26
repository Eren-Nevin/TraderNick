<script lang="ts">
  // Shared "Trigger now" (debug) button for the notification widgets. Fires the
  // rule immediately, bypassing its cadence — the monitor's trigger-poller picks
  // it up within seconds. `beforeTrigger` (usually the widget's sync()) persists
  // the latest config first so the monitor reads it.
  import { stopDragEvents } from '$lib/actions/stopDragEvents';

  let {
    ruleId,
    beforeTrigger
  }: { ruleId: string; beforeTrigger?: () => Promise<void> | void } = $props();

  let state = $state<'idle' | 'sending' | 'ok' | 'err'>('idle');
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function trigger() {
    if (state === 'sending') return;
    state = 'sending';
    try {
      if (beforeTrigger) await beforeTrigger();
      const res = await fetch(`/api/notifications/rules/${encodeURIComponent(ruleId)}/trigger`, {
        method: 'POST'
      });
      state = res.ok ? 'ok' : 'err';
    } catch {
      state = 'err';
    }
    clearTimeout(timer);
    timer = setTimeout(() => (state = 'idle'), 2500);
  }

  const label = $derived(
    state === 'sending' ? '⚡ …' : state === 'ok' ? '⚡ Sent' : state === 'err' ? '⚡ Failed' : '⚡ Test'
  );
</script>

<button
  type="button"
  use:stopDragEvents
  class="shrink-0 rounded border px-1.5 py-0.5 text-[10px] transition-colors {state === 'ok'
    ? 'border-emerald-600 text-emerald-300'
    : state === 'err'
      ? 'border-red-600 text-red-300'
      : 'border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200'}"
  title="Trigger now (debug) — fire this notification immediately, ignoring the cadence"
  disabled={state === 'sending'}
  onclick={trigger}>{label}</button>
