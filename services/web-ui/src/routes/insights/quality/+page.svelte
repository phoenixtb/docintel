<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch } from '$lib/api';
  import { getAuthState, authStore } from '$lib/auth';
  import { toast } from 'svelte-sonner';
  import InsightsChart from '$lib/components/InsightsChart.svelte';

  let _authState = $state(getAuthState());
  authStore.subscribe((s) => { _authState = s; });
  const isPlatformAdmin = $derived(_authState.user?.role === 'platform_admin');

  interface QualitySummary {
    window: string;
    total_queries: number;
    abstention_rate: number;
    reranker_degraded_count: number;
    feedback: { likes: number; dislikes: number; total: number; like_rate: number };
    timeseries: { ts: string; total: number; abstention_rate: number; reranker_degraded_count: number }[];
  }
  interface TopQueries {
    frequent_queries: { query_text: string; count: number }[];
    zero_result_queries: { query_text: string; count: number }[];
  }

  let windowSel: '24h' | '7d' | '30d' = $state('7d');
  let loading = $state(true);
  let quality: QualitySummary | null = $state(null);
  let topQueries: TopQueries | null = $state(null);
  let allTenants: { tenantId: string; name: string }[] = $state([]);
  let selectedTenant = $state('');

  async function fetchJson(url: string) {
    const res = await apiFetch(url);
    return res.ok ? res.json() : null;
  }

  function tenantParam(): string {
    return selectedTenant ? `&tenant_id=${encodeURIComponent(selectedTenant)}` : '';
  }

  async function load() {
    loading = true;
    try {
      const [q, tq] = await Promise.all([
        fetchJson(`/api/v1/analytics/quality?window=${windowSel}${tenantParam()}`),
        fetchJson(`/api/v1/analytics/top-queries?window=${windowSel}&limit=10${tenantParam()}`),
      ]);
      quality = q;
      topQueries = tq;
    } catch (e) {
      toast.error(`Failed to load quality metrics: ${e}`);
    }
    loading = false;
  }

  onMount(async () => {
    if (isPlatformAdmin) {
      const data = await fetchJson('/api/v1/tenants');
      allTenants = data ?? [];
    }
    await load();
  });
</script>

<div class="space-y-5">
  <div class="flex items-center gap-3 flex-wrap">
    <div class="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
      {#each ['24h', '7d', '30d'] as w}
        <button
          onclick={() => { windowSel = w as typeof windowSel; load(); }}
          class="px-3 py-1 text-xs font-medium rounded-md transition-colors
            {windowSel === w ? 'bg-white dark:bg-gray-700 shadow text-gray-900 dark:text-white' : 'text-gray-500 dark:text-gray-400'}"
        >{w}</button>
      {/each}
    </div>

    {#if isPlatformAdmin && allTenants.length > 0}
      <select
        bind:value={selectedTenant}
        onchange={load}
        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
          bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-1 focus:ring-blue-500"
      >
        <option value="">All tenants</option>
        {#each allTenants as t}
          <option value={t.tenantId}>{t.name} ({t.tenantId})</option>
        {/each}
      </select>
    {/if}
  </div>

  {#if loading}
    <div class="text-center py-12 text-gray-400">Loading quality metrics…</div>
  {:else}
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      {#each [
        { label: 'Total Queries', value: quality?.total_queries?.toLocaleString() ?? '—' },
        { label: 'Abstention Rate', value: quality ? `${(quality.abstention_rate * 100).toFixed(1)}%` : '—' },
        { label: 'Reranker Degraded', value: quality?.reranker_degraded_count?.toLocaleString() ?? '—' },
        { label: 'Feedback Like Rate', value: quality ? `${(quality.feedback.like_rate * 100).toFixed(1)}% (${quality.feedback.total})` : '—' },
      ] as card}
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
          <p class="text-xs text-gray-500 dark:text-gray-400">{card.label}</p>
          <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{card.value}</p>
        </div>
      {/each}
    </div>

    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Abstention rate over time</p>
      {#if quality && quality.timeseries.length > 0}
        <InsightsChart
          type="bar"
          labels={quality.timeseries.map((p) => p.ts.slice(0, 10))}
          datasets={[{ label: 'Abstention %', data: quality.timeseries.map((p) => Math.round(p.abstention_rate * 1000) / 10), color: '#ef4444' }]}
          yTicksSuffix="%"
        />
      {:else}
        <p class="text-sm text-gray-400 py-8 text-center">No data yet.</p>
      {/if}
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Top queries</p>
        {#if topQueries && topQueries.frequent_queries.length > 0}
          <ul class="divide-y divide-gray-100 dark:divide-gray-700">
            {#each topQueries.frequent_queries as q}
              <li class="py-2 flex items-center justify-between gap-3">
                <span class="text-sm text-gray-700 dark:text-gray-300 truncate">{q.query_text}</span>
                <span class="text-xs font-mono text-gray-400 shrink-0">{q.count}</span>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="text-sm text-gray-400 py-4 text-center">No queries recorded yet.</p>
        {/if}
      </div>

      <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">
          Zero-result queries <span class="text-gray-400 font-normal">(corpus gaps)</span>
        </p>
        {#if topQueries && topQueries.zero_result_queries.length > 0}
          <ul class="divide-y divide-gray-100 dark:divide-gray-700">
            {#each topQueries.zero_result_queries as q}
              <li class="py-2 flex items-center justify-between gap-3">
                <span class="text-sm text-gray-700 dark:text-gray-300 truncate">{q.query_text}</span>
                <span class="text-xs font-mono text-red-500 shrink-0">{q.count}</span>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="text-sm text-gray-400 py-4 text-center">No zero-result queries in this window.</p>
        {/if}
      </div>
    </div>
  {/if}
</div>
