<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch } from '$lib/api';
  import { getAuthState, authStore } from '$lib/auth';
  import { toast } from 'svelte-sonner';
  import InsightsChart from '$lib/components/InsightsChart.svelte';

  let _authState = $state(getAuthState());
  authStore.subscribe((s) => { _authState = s; });
  const isPlatformAdmin = $derived(_authState.user?.role === 'platform_admin');

  interface UsageSummary {
    window: string;
    total_queries: number;
    unique_users: number;
    p50_latency_ms: number;
    p95_latency_ms: number;
    cache_hit_rate: number;
  }
  interface TimeseriesPoint {
    ts: string;
    count: number;
    avg_latency_ms: number;
    p95_latency_ms?: number;
    cache_hit_rate?: number;
  }

  let windowSel: '24h' | '7d' | '30d' = $state('7d');
  let loading = $state(true);
  let usage: UsageSummary | null = $state(null);
  let timeseries: TimeseriesPoint[] = $state([]);
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
      const days = windowSel === '24h' ? 1 : windowSel === '30d' ? 30 : 7;
      const [u, ts] = await Promise.all([
        fetchJson(`/api/v1/analytics/usage?window=${windowSel}${tenantParam()}`),
        fetchJson(`/api/v1/analytics/queries/timeseries?days=${days}${tenantParam()}`),
      ]);
      usage = u;
      timeseries = ts ?? [];
    } catch (e) {
      toast.error(`Failed to load usage: ${e}`);
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
    <div class="text-center py-12 text-gray-400">Loading usage…</div>
  {:else}
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      {#each [
        { label: 'Total Queries', value: usage?.total_queries?.toLocaleString() ?? '—' },
        { label: 'Active Users', value: usage?.unique_users?.toLocaleString() ?? '—' },
        { label: 'p50 / p95 Latency', value: usage ? `${Math.round(usage.p50_latency_ms)} / ${Math.round(usage.p95_latency_ms)}ms` : '—' },
        { label: 'Cache Hit Rate', value: usage ? `${(usage.cache_hit_rate * 100).toFixed(1)}%` : '—' },
      ] as card}
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
          <p class="text-xs text-gray-500 dark:text-gray-400">{card.label}</p>
          <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{card.value}</p>
        </div>
      {/each}
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Query volume</p>
        {#if timeseries.length > 0}
          <InsightsChart
            labels={timeseries.map((p) => p.ts.slice(0, 10))}
            datasets={[{ label: 'Queries', data: timeseries.map((p) => p.count), color: '#3b82f6' }]}
          />
        {:else}
          <p class="text-sm text-gray-400 py-8 text-center">No data yet.</p>
        {/if}
      </div>
      <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Latency percentiles (ms)</p>
        {#if timeseries.length > 0}
          <InsightsChart
            labels={timeseries.map((p) => p.ts.slice(0, 10))}
            datasets={[
              { label: 'Avg', data: timeseries.map((p) => p.avg_latency_ms), color: '#6366f1' },
              { label: 'p95', data: timeseries.map((p) => p.p95_latency_ms ?? 0), color: '#f59e0b' },
            ]}
          />
        {:else}
          <p class="text-sm text-gray-400 py-8 text-center">No data yet.</p>
        {/if}
      </div>
    </div>
  {/if}
</div>
