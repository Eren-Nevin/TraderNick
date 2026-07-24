<script lang="ts">
  // Notifications admin — configure the two Telegram bots (user + admin) and
  // the built-in admin alert monitors (job failures + stale data). Bot tokens
  // and admin-rule config are written through /api/admin/config/notification_*
  // (→ admin_server → ClickHouse) and picked up by the monitor + bot processes
  // within the config cache TTL — no restart.

  import { onMount } from 'svelte';

  type BotRow = { bot: string; configured: boolean; token_masked: string };
  type Rule = {
    rule_id: string; kind: string; scope: string; enabled: boolean;
    cadence_s: number; cooldown_s: number; params: Record<string, unknown>; title: string;
  };
  type Topic = { topic_id: string; title: string; grp: string; enabled: boolean };

  let bots = $state<BotRow[]>([]);
  let rules = $state<Rule[]>([]);
  let topics = $state<Topic[]>([]);
  let loading = $state(true);
  let loadErr = $state<string | null>(null);
  let msg = $state<string | null>(null);

  // New-token inputs (one per bot). Blank = leave unchanged.
  let tokenInput = $state<Record<string, string>>({ user: '', admin: '' });
  let busy = $state<Record<string, boolean>>({});

  async function load() {
    loading = true;
    try {
      const [b, r] = await Promise.all([
        fetch('/api/admin/config/notification_bots'),
        fetch('/api/admin/config/notification_rules')
      ]);
      if (!b.ok) throw new Error(`bots ${b.status} ${await b.text()}`);
      if (!r.ok) throw new Error(`rules ${r.status} ${await r.text()}`);
      bots = (await b.json()).bots ?? [];
      const rb = await r.json();
      rules = rb.rules ?? [];
      topics = rb.topics ?? [];
      loadErr = null;
    } catch (e) {
      loadErr = String(e);
    } finally {
      loading = false;
    }
  }

  async function saveBot(bot: string) {
    const token = (tokenInput[bot] ?? '').trim();
    if (!token) { msg = 'Paste a bot token first.'; return; }
    busy[bot] = true;
    msg = null;
    try {
      const res = await fetch('/api/admin/config/notification_bots', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ bot, token })
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      msg = `Saved ${bot} bot token.`;
      tokenInput[bot] = '';
      await load();
    } catch (e) {
      msg = `Save failed: ${e}`;
    } finally {
      busy[bot] = false;
    }
  }

  function grace(rule: Rule): number {
    const g = (rule.params as any)?.grace_s;
    return typeof g === 'number' ? g : 300;
  }

  async function saveRule(rule: Rule, patch: Partial<Rule> & { grace_s?: number }) {
    busy[rule.rule_id] = true;
    msg = null;
    try {
      const params: Record<string, unknown> = { ...rule.params };
      if (patch.grace_s !== undefined) params.grace_s = patch.grace_s;
      const body = {
        rule_id: rule.rule_id,
        enabled: patch.enabled ?? rule.enabled,
        cadence_s: patch.cadence_s ?? rule.cadence_s,
        cooldown_s: patch.cooldown_s ?? rule.cooldown_s,
        params
      };
      const res = await fetch('/api/admin/config/notification_rules', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      msg = `Saved “${rule.title}”.`;
      await load();
    } catch (e) {
      msg = `Save failed: ${e}`;
    } finally {
      busy[rule.rule_id] = false;
    }
  }

  onMount(load);
</script>

<div class="mx-auto max-w-3xl p-6 text-zinc-200">
  <h1 class="mb-1 text-xl font-semibold text-zinc-100">Notifications</h1>
  <p class="mb-6 text-sm text-zinc-400">
    Telegram alert delivery. Register the two bots, then configure the built-in
    admin monitors. Users subscribe to topics from inside the bots themselves.
  </p>

  {#if msg}
    <div class="mb-4 rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-300">{msg}</div>
  {/if}
  {#if loadErr}
    <div class="mb-4 rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">{loadErr}</div>
  {/if}

  {#if loading}
    <div class="text-sm text-zinc-500">Loading…</div>
  {:else}
    <!-- Bots -->
    <section class="mb-8">
      <h2 class="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">Bots</h2>
      <div class="space-y-3">
        {#each ['user', 'admin'] as bot}
          {@const row = bots.find((b) => b.bot === bot)}
          <div class="rounded border border-zinc-800 bg-zinc-900/50 p-3">
            <div class="mb-2 flex items-center justify-between">
              <div class="font-medium capitalize text-zinc-100">{bot} bot</div>
              <div class="text-xs {row?.configured ? 'text-emerald-400' : 'text-zinc-500'}">
                {row?.configured ? `configured · ${row.token_masked}` : 'not configured'}
              </div>
            </div>
            {#if bot === 'admin'}
              <p class="mb-2 text-xs text-zinc-500">
                Requires users to reply with the admin secret (NOTIFICATIONS_ADMIN_SECRET) before subscribing.
              </p>
            {/if}
            <div class="flex gap-2">
              <input
                class="flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-200 placeholder-zinc-600"
                type="password"
                autocomplete="off"
                placeholder="paste bot token to set / replace"
                bind:value={tokenInput[bot]} />
              <button
                class="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-600 disabled:opacity-50"
                disabled={busy[bot]}
                onclick={() => saveBot(bot)}>Save</button>
            </div>
          </div>
        {/each}
      </div>
    </section>

    <!-- Admin alert rules -->
    <section class="mb-8">
      <h2 class="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">Admin alerts</h2>
      <div class="space-y-3">
        {#each rules as rule (rule.rule_id)}
          <div class="rounded border border-zinc-800 bg-zinc-900/50 p-3">
            <div class="mb-2 flex items-center justify-between">
              <div class="font-medium text-zinc-100">{rule.title}</div>
              <label class="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  disabled={busy[rule.rule_id]}
                  onchange={(e) => saveRule(rule, { enabled: (e.target as HTMLInputElement).checked })} />
                <span class={rule.enabled ? 'text-emerald-400' : 'text-zinc-500'}>
                  {rule.enabled ? 'enabled' : 'disabled'}
                </span>
              </label>
            </div>
            <div class="flex flex-wrap items-end gap-4">
              <label class="text-xs text-zinc-400">
                Check every (s)
                <input
                  class="mt-1 block w-24 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-200"
                  type="number" min="15" value={rule.cadence_s}
                  onchange={(e) => saveRule(rule, { cadence_s: +(e.target as HTMLInputElement).value })} />
              </label>
              {#if rule.kind === 'admin_stale_data'}
                <label class="text-xs text-zinc-400">
                  Stale grace (s)
                  <input
                    class="mt-1 block w-24 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-200"
                    type="number" min="0" value={grace(rule)}
                    onchange={(e) => saveRule(rule, { grace_s: +(e.target as HTMLInputElement).value })} />
                </label>
              {/if}
              <span class="text-xs text-zinc-600">{rule.kind}</span>
            </div>
          </div>
        {/each}
        {#if rules.length === 0}
          <div class="text-sm text-zinc-500">No admin rules seeded yet.</div>
        {/if}
      </div>
    </section>

    <!-- Admin topics (read-only) -->
    <section>
      <h2 class="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
        Admin topics (auto, one per stream group)
      </h2>
      <div class="flex flex-wrap gap-2">
        {#each topics as t (t.topic_id)}
          <span class="rounded-full border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-xs text-zinc-300">
            {t.title}
          </span>
        {/each}
        {#if topics.length === 0}
          <span class="text-sm text-zinc-500">None seeded.</span>
        {/if}
      </div>
    </section>
  {/if}
</div>
