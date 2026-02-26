<script lang="ts">
  import { onMount } from 'svelte';
  import { env } from '$env/dynamic/public';
  import { getAuthHeaders, getTenantId } from '$lib/auth';
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  
  const API_BASE = env.PUBLIC_API_URL || 'http://localhost:8080';
  
  // State
  let activeTab: 'dashboard' | 'documents' | 'cache' | 'tenants' = $state('dashboard');
  let loading = $state(true);
  
  // Dashboard data
  let health: any = $state(null);
  let stats: any = $state(null);
  let vectorStats: any = $state(null);
  let querySummary: any = $state(null);
  let feedbackSummary: any = $state(null);
  
  // Cache data
  let cacheStats: any = $state(null);
  let cacheClearing = $state(false);
  
  // Tenants data
  let tenants: any[] = $state([]);
  let selectedTenant: any = $state(null);
  let tenantUsage: any = $state(null);
  
  // Bulk delete
  let deletingDocs = $state(false);
  let deleteResult: string | null = $state(null);

  // Confirm dialog state
  let confirmOpen = $state(false);
  let confirmTenantId: string | null = $state(null);
  
  const headers = () => ({ ...getAuthHeaders() });
  
  async function fetchJson(url: string) {
    const res = await fetch(`${API_BASE}${url}`, { headers: headers() });
    return res.ok ? res.json() : null;
  }
  
  async function loadDashboard() {
    loading = true;
    try {
      [health, stats, vectorStats, querySummary, feedbackSummary] = await Promise.all([
        fetchJson('/api/v1/admin/health'),
        fetchJson('/api/v1/admin/stats'),
        fetchJson('/api/v1/vector-stats'),
        fetchJson('/api/v1/analytics/queries/summary'),
        fetchJson('/api/v1/analytics/feedback/summary'),
      ]);
    } catch (e) { console.error(e); }
    loading = false;
  }
  
  async function loadCache() {
    cacheStats = await fetchJson('/api/v1/admin/cache/stats');
  }
  
  async function clearCache(tenantId?: string) {
    cacheClearing = true;
    try {
      const url = tenantId ? `/api/v1/admin/cache/clear/${tenantId}` : '/api/v1/admin/cache/clear';
      await fetch(`${API_BASE}${url}`, { method: 'POST', headers: headers() });
      await loadCache();
    } catch (e) { console.error(e); }
    cacheClearing = false;
  }
  
  async function loadTenants() {
    tenants = (await fetchJson('/api/v1/tenants')) || [];
  }
  
  async function loadTenantUsage(tenantId: string) {
    selectedTenant = tenantId;
    tenantUsage = await fetchJson(`/api/v1/tenants/${tenantId}/usage`);
  }
  
  function deleteAllDocuments(tenantId: string) {
    confirmTenantId = tenantId;
    confirmOpen = true;
  }

  async function doDeleteAllDocuments() {
    if (!confirmTenantId) return;
    const tenantId = confirmTenantId;
    confirmOpen = false;
    confirmTenantId = null;
    deletingDocs = true;
    deleteResult = null;
    try {
      const res = await fetch(`${API_BASE}/api/v1/documents/all?tenant_id=${tenantId}`, {
        method: 'DELETE',
        headers: headers(),
      });
      if (res.ok) {
        const data = await res.json();
        deleteResult = `Deleted ${data.deleted} documents for tenant "${tenantId}"`;
        toast.success(deleteResult);
        await loadDashboard();
        await loadTenants();
      } else {
        deleteResult = `Failed: ${res.status}`;
        toast.error(deleteResult);
      }
    } catch (e) {
      deleteResult = `Error: ${e}`;
      toast.error(deleteResult);
    }
    deletingDocs = false;
  }
  
  function switchTab(tab: typeof activeTab) {
    activeTab = tab;
    deleteResult = null;
    if (tab === 'dashboard') loadDashboard();
    if (tab === 'cache') loadCache();
    if (tab === 'tenants') loadTenants();
  }
  
  onMount(() => {
    loadDashboard();
    loadTenants();
  });
</script>

<div class="h-full overflow-y-auto">
  <div class="max-w-6xl mx-auto p-6">
    <h1 class="text-2xl font-bold text-gray-900 dark:text-white mb-6">Admin</h1>
    
    <!-- Tabs -->
    <div class="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700">
      {#each [
        { id: 'dashboard', label: 'Dashboard' },
        { id: 'documents', label: 'Documents' },
        { id: 'cache', label: 'Cache' },
        { id: 'tenants', label: 'Tenants' },
      ] as tab}
        <button
          onclick={() => switchTab(tab.id as typeof activeTab)}
          class="px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors
            {activeTab === tab.id 
              ? 'border-blue-600 text-blue-600 dark:text-blue-400' 
              : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300'}"
        >
          {tab.label}
        </button>
      {/each}
    </div>
    
    <!-- Dashboard Tab -->
    {#if activeTab === 'dashboard'}
      {#if loading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else}
        <!-- Health Status -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          {#if health?.components}
            {#each Object.entries(health.components) as [name, status]}
              <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <div class="flex items-center justify-between">
                  <span class="text-sm font-medium text-gray-700 dark:text-gray-300 capitalize">{name}</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium
                    {(status as any) === 'UP' || (status as any)?.status === 'UP'
                      ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'}">
                    {(status as any) === 'UP' || (status as any)?.status === 'UP' ? 'Healthy' : 'Down'}
                  </span>
                </div>
              </div>
            {/each}
          {/if}
        </div>
        
        <!-- Stats Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {#each [
            { label: 'Documents', value: stats?.totalDocuments ?? '-' },
            { label: 'Chunks', value: stats?.totalChunks ?? '-' },
            { label: 'Queries', value: stats?.totalQueries ?? '-' },
            { label: 'Tenants', value: stats?.totalTenants ?? '-' },
          ] as card}
            <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <p class="text-sm text-gray-500 dark:text-gray-400">{card.label}</p>
              <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{card.value}</p>
            </div>
          {/each}
        </div>
        
        <!-- Analytics Stats -->
        {#if querySummary || feedbackSummary}
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            {#each [
              { label: 'Total Queries', value: querySummary?.totalQueries ?? '-' },
              { label: 'Avg Latency', value: querySummary ? `${Math.round(querySummary.avgLatencyMs)}ms` : '-' },
              { label: 'Cache Hit Rate', value: querySummary ? `${(querySummary.cacheHitRate * 100).toFixed(1)}%` : '-' },
              { label: 'P95 Latency', value: querySummary ? `${querySummary.p95LatencyMs}ms` : '-' },
            ] as card}
              <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <p class="text-sm text-gray-500 dark:text-gray-400">{card.label}</p>
                <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{card.value}</p>
              </div>
            {/each}
          </div>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            {#each [
              { label: 'Total Feedback', value: feedbackSummary?.totalFeedback ?? '-' },
              { label: 'Likes', value: feedbackSummary?.likes ?? '-' },
              { label: 'Dislikes', value: feedbackSummary?.dislikes ?? '-' },
              { label: 'Like Rate', value: feedbackSummary ? `${(feedbackSummary.likeRate * 100).toFixed(1)}%` : '-' },
            ] as card}
              <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <p class="text-sm text-gray-500 dark:text-gray-400">{card.label}</p>
                <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{card.value}</p>
              </div>
            {/each}
          </div>
        {/if}

        <!-- Vector Stats -->
        {#if vectorStats}
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
            <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Vector Store</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400">Total vectors: <span class="font-mono font-medium text-gray-900 dark:text-white">{vectorStats.total_vectors ?? 0}</span></p>
            {#if vectorStats.tenant_stats}
              <div class="mt-2 space-y-1">
                {#each Object.entries(vectorStats.tenant_stats) as [tenant, count]}
                  <p class="text-sm text-gray-500 dark:text-gray-400">
                    {tenant}: <span class="font-mono">{count}</span> vectors
                  </p>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      {/if}
    {/if}
    
    <!-- Documents Tab (Bulk Operations) -->
    {#if activeTab === 'documents'}
      <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-4">Bulk Document Operations</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-6">
          Delete all documents for a tenant. This removes documents, chunks, vectors, and uploaded files.
        </p>
        
        {#if deleteResult}
          <div class="mb-4 p-3 rounded-lg text-sm
            {deleteResult.startsWith('Deleted') 
              ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400' 
              : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'}">
            {deleteResult}
          </div>
        {/if}
        
        <div class="space-y-3">
          {#each tenants as tenant}
            <div class="flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50">
              <div>
                <p class="text-sm font-medium text-gray-900 dark:text-white">{tenant.name || tenant.tenantId}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400">{tenant.documentCount ?? 0} documents</p>
              </div>
              <button
                onclick={() => deleteAllDocuments(tenant.tenantId)}
                disabled={deletingDocs || (tenant.documentCount ?? 0) === 0}
                class="px-3 py-1.5 text-xs font-medium rounded-lg
                  bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400
                  hover:bg-red-100 dark:hover:bg-red-900/40
                  disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {deletingDocs ? 'Deleting...' : 'Delete All'}
              </button>
            </div>
          {/each}
          {#if tenants.length === 0}
            <p class="text-sm text-gray-400">No tenants found.</p>
          {/if}
        </div>
      </div>
    {/if}
    
    <!-- Cache Tab -->
    {#if activeTab === 'cache'}
      <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white">Semantic Cache</h3>
          <button
            onclick={() => clearCache()}
            disabled={cacheClearing}
            class="px-4 py-2 text-sm font-medium rounded-lg
              bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400
              hover:bg-red-100 dark:hover:bg-red-900/40
              disabled:opacity-50 transition-colors"
          >
            {cacheClearing ? 'Clearing...' : 'Clear All Cache'}
          </button>
        </div>
        
        {#if cacheStats}
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p class="text-sm text-gray-500 dark:text-gray-400">Entries</p>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{cacheStats.totalEntries ?? 0}</p>
            </div>
            <div>
              <p class="text-sm text-gray-500 dark:text-gray-400">Hit Rate</p>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{cacheStats.hitRate ? `${(cacheStats.hitRate * 100).toFixed(1)}%` : 'N/A'}</p>
            </div>
            <div>
              <p class="text-sm text-gray-500 dark:text-gray-400">Avg Latency Saved</p>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{cacheStats.avgLatencySavedMs ? `${cacheStats.avgLatencySavedMs}ms` : 'N/A'}</p>
            </div>
            <div>
              <p class="text-sm text-gray-500 dark:text-gray-400">Newest Entry</p>
              <p class="text-sm font-medium text-gray-900 dark:text-white">{cacheStats.newestEntry || 'None'}</p>
            </div>
          </div>
        {:else}
          <p class="text-sm text-gray-400">Loading cache stats...</p>
        {/if}
        
        <!-- Per-tenant clear -->
        {#if tenants.length > 0}
          <div class="mt-6 pt-4 border-t border-gray-200 dark:border-gray-700">
            <p class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Clear by Tenant</p>
            <div class="flex flex-wrap gap-2">
              {#each tenants as tenant}
                <button
                  onclick={() => clearCache(tenant.tenantId)}
                  disabled={cacheClearing}
                  class="px-3 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600
                    text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700
                    disabled:opacity-50 transition-colors"
                >
                  Clear {tenant.tenantId}
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </div>
    {/if}
    
    <!-- Tenants Tab -->
    {#if activeTab === 'tenants'}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Tenant list -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-4">Tenants</h3>
          <div class="space-y-2">
            {#each tenants as tenant}
              <button
                onclick={() => loadTenantUsage(tenant.tenantId)}
                class="w-full text-left p-3 rounded-lg transition-colors
                  {selectedTenant === tenant.tenantId 
                    ? 'bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800' 
                    : 'hover:bg-gray-50 dark:hover:bg-gray-700/50 border border-transparent'}"
              >
                <p class="text-sm font-medium text-gray-900 dark:text-white">{tenant.name || tenant.tenantId}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  {tenant.documentCount ?? 0} docs, {tenant.queryCount ?? 0} queries
                </p>
              </button>
            {/each}
            {#if tenants.length === 0}
              <p class="text-sm text-gray-400">No tenants found.</p>
            {/if}
          </div>
        </div>
        
        <!-- Tenant usage detail -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          {#if tenantUsage && selectedTenant}
            <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-4">
              Usage: {selectedTenant}
            </h3>
            <div class="grid grid-cols-2 gap-4">
              {#each [
                { label: 'Documents', value: tenantUsage.documentCount ?? 0 },
                { label: 'Chunks', value: tenantUsage.chunkCount ?? 0 },
                { label: 'Total Queries', value: tenantUsage.totalQueries ?? 0 },
                { label: 'Queries (24h)', value: tenantUsage.queriesLast24h ?? 0 },
                { label: 'Cache Hit Rate', value: tenantUsage.cacheHitRate ? `${(tenantUsage.cacheHitRate * 100).toFixed(1)}%` : 'N/A' },
                { label: 'Storage', value: tenantUsage.storageBytes ? `${(tenantUsage.storageBytes / 1024 / 1024).toFixed(1)} MB` : '0 MB' },
              ] as item}
                <div>
                  <p class="text-sm text-gray-500 dark:text-gray-400">{item.label}</p>
                  <p class="text-lg font-bold text-gray-900 dark:text-white">{item.value}</p>
                </div>
              {/each}
            </div>
            {#if tenantUsage.lastQueryAt}
              <p class="text-xs text-gray-400 mt-4">Last query: {new Date(tenantUsage.lastQueryAt).toLocaleString()}</p>
            {/if}
          {:else}
            <div class="text-center py-12 text-gray-400 dark:text-gray-500">
              <p class="text-sm">Select a tenant to view usage details</p>
            </div>
          {/if}
        </div>
      </div>
    {/if}
  </div>
</div>

<ConfirmDialog
  open={confirmOpen}
  title="Delete all documents?"
  message={confirmTenantId ? `This will permanently delete ALL documents, chunks, and vectors for tenant "${confirmTenantId}". This cannot be undone.` : ''}
  confirmLabel="Delete All"
  dangerous={true}
  onconfirm={doDeleteAllDocuments}
  oncancel={() => { confirmOpen = false; confirmTenantId = null; }}
/>
