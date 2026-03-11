<script lang="ts">
  import { onMount } from 'svelte';
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  import { apiFetch } from '$lib/api';
  
  // State
  let activeTab: 'dashboard' | 'documents' | 'cache' | 'tenants' | 'tenant-mgmt' | 'users' | 'model' = $state('dashboard');
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
  let confirmMode: 'delete-docs' | 'delete-tenant' = $state('delete-docs');

  // Tenant management
  let newTenant = $state({ id: '', name: '', quotaDocuments: 1000, quotaQueriesPerDay: 10000 });
  let creatingTenant = $state(false);
  let editingTenantId: string | null = $state(null);
  let editTenant = $state({ name: '', quotaDocuments: 0, quotaQueriesPerDay: 0 });
  let savingTenant = $state(false);
  let deletingTenant = $state(false);

  // Users
  interface TenantUser { id: string; email: string; username: string; name: string; role: string; tenantId: string }
  let users: TenantUser[] = $state([]);
  let usersLoading = $state(false);
  let selectedUserTenant = $state('');
  let updatingRoleFor: string | null = $state(null);

  // Platform model
  interface ModelInfo { name: string; size?: number }
  interface PlatformSettings { llmModel: string | null }
  let availableModels: ModelInfo[] = $state([]);
  let platformSettings: PlatformSettings | null = $state(null);
  let selectedPlatformModel: string | null = $state(null); // null = "Tenant Choice"
  let modelLoading = $state(false);
  let modelSaving = $state(false);
  let platformModelConfirmOpen = $state(false);
  let pendingPlatformModel: string | null | undefined = $state(undefined);

  const jsonHeaders = () => ({ 'Content-Type': 'application/json' });

  async function fetchJson(url: string) {
    const res = await apiFetch(url);
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
      await apiFetch(url, { method: 'POST' });
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

  // ---- Bulk doc delete ----
  
  function deleteAllDocuments(tenantId: string) {
    confirmTenantId = tenantId;
    confirmMode = 'delete-docs';
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
      const res = await apiFetch(`/api/v1/documents/all?tenant_id=${tenantId}`, { method: 'DELETE' });
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

  // ---- Tenant CRUD ----

  async function createTenant() {
    if (!newTenant.id.trim() || !newTenant.name.trim()) { toast.error('Tenant ID and name are required'); return; }
    creatingTenant = true;
    try {
      const res = await apiFetch(`/api/v1/tenants`, {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({
          id: newTenant.id.trim(),
          name: newTenant.name.trim(),
          quotaDocuments: newTenant.quotaDocuments,
          quotaQueriesPerDay: newTenant.quotaQueriesPerDay,
        }),
      });
      if (res.ok) {
        toast.success(`Tenant "${newTenant.name}" created`);
        newTenant = { id: '', name: '', quotaDocuments: 1000, quotaQueriesPerDay: 10000 };
        await loadTenants();
      } else {
        toast.error(`Failed: ${res.status}`);
      }
    } catch (e) { toast.error(`Error: ${e}`); }
    creatingTenant = false;
  }

  function startEditTenant(tenant: any) {
    editingTenantId = tenant.tenantId;
    editTenant = { name: tenant.name, quotaDocuments: tenant.quotaDocuments ?? 1000, quotaQueriesPerDay: tenant.quotaQueriesPerDay ?? 10000 };
  }

  async function saveEditTenant() {
    if (!editingTenantId) return;
    savingTenant = true;
    try {
      const res = await apiFetch(`/api/v1/tenants/${editingTenantId}`, {
        method: 'PUT',
        headers: jsonHeaders(),
        body: JSON.stringify({
          name: editTenant.name || null,
          quotaDocuments: editTenant.quotaDocuments || null,
          quotaQueriesPerDay: editTenant.quotaQueriesPerDay || null,
        }),
      });
      if (res.ok) {
        toast.success('Tenant updated');
        editingTenantId = null;
        await loadTenants();
      } else {
        toast.error(`Failed: ${res.status}`);
      }
    } catch (e) { toast.error(`Error: ${e}`); }
    savingTenant = false;
  }

  function confirmDeleteTenant(tenantId: string) {
    confirmTenantId = tenantId;
    confirmMode = 'delete-tenant';
    confirmOpen = true;
  }

  async function doDeleteTenant() {
    if (!confirmTenantId) return;
    const tenantId = confirmTenantId;
    confirmOpen = false;
    confirmTenantId = null;
    deletingTenant = true;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success(`Tenant "${tenantId}" deleted`);
        await loadTenants();
        await loadDashboard();
      } else {
        toast.error(`Failed: ${res.status}`);
      }
    } catch (e) { toast.error(`Error: ${e}`); }
    deletingTenant = false;
  }

  async function handleConfirm() {
    if (confirmMode === 'delete-docs') await doDeleteAllDocuments();
    else await doDeleteTenant();
  }

  // ---- Users ----

  async function loadUsers(tenantId?: string) {
    if (!tenantId && !selectedUserTenant) return;
    const tid = tenantId ?? selectedUserTenant;
    usersLoading = true;
    users = (await fetchJson(`/api/v1/tenants/${tid}/users`)) ?? [];
    usersLoading = false;
  }

  async function updateUserRole(user: TenantUser, newRole: string) {
    updatingRoleFor = user.id;
    try {
      const res = await apiFetch(`/api/v1/tenants/${user.tenantId}/users/${user.id}/role`, {
        method: 'PUT',
        headers: jsonHeaders(),
        body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) {
        toast.success(`Updated role for ${user.username}`);
        await loadUsers();
      } else {
        toast.error(`Failed: ${res.status}`);
      }
    } catch (e) { toast.error(`Error: ${e}`); }
    updatingRoleFor = null;
  }
  
  async function loadPlatformModel() {
    modelLoading = true;
    try {
      const [modelsRes, settingsRes] = await Promise.all([
        apiFetch('/api/v1/models'),
        apiFetch('/api/v1/admin/platform/settings'),
      ]);
      if (modelsRes.ok) availableModels = (await modelsRes.json()).models ?? [];
      if (settingsRes.ok) {
        platformSettings = await settingsRes.json();
        selectedPlatformModel = platformSettings?.llmModel ?? null;
      }
    } catch (e) { toast.error(`Failed to load model settings: ${e}`); }
    modelLoading = false;
  }

  function requestPlatformModelChange(model: string | null) {
    pendingPlatformModel = model;
    platformModelConfirmOpen = true;
  }

  async function confirmPlatformModelChange() {
    platformModelConfirmOpen = false;
    modelSaving = true;
    try {
      const res = await apiFetch('/api/v1/admin/platform/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ llmModel: pendingPlatformModel ?? null }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      // Clear ALL tenant caches — model changed globally
      await apiFetch('/api/v1/admin/cache/clear', { method: 'POST' });
      platformSettings = await res.json();
      selectedPlatformModel = platformSettings?.llmModel ?? null;
      toast.success('Platform model updated and all caches cleared.');
    } catch (e) { toast.error(`Failed: ${e}`); }
    modelSaving = false;
    pendingPlatformModel = undefined;
  }

  function switchTab(tab: typeof activeTab) {
    activeTab = tab;
    deleteResult = null;
    if (tab === 'dashboard') loadDashboard();
    if (tab === 'cache') loadCache();
    if (tab === 'tenants') loadTenants();
    if (tab === 'tenant-mgmt') loadTenants();
    if (tab === 'users') { loadTenants(); users = []; }
    if (tab === 'model') loadPlatformModel();
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
    <div class="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700 flex-wrap">
      {#each [
        { id: 'dashboard', label: 'Dashboard' },
        { id: 'documents', label: 'Documents' },
        { id: 'cache', label: 'Cache' },
        { id: 'tenants', label: 'Usage' },
        { id: 'tenant-mgmt', label: 'Tenants' },
        { id: 'users', label: 'Users' },
        { id: 'model', label: 'Model' },
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
    <!-- Tenant Management Tab -->
    {#if activeTab === 'tenant-mgmt'}
      <div class="space-y-6">
        <!-- Create tenant -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-4">Create Tenant</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label for="new-tenant-id" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Tenant ID</label>
              <input
                id="new-tenant-id"
                type="text"
                bind:value={newTenant.id}
                placeholder="e.g. acme"
                class="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                  bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                  focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            <div>
              <label for="new-tenant-name" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Display Name</label>
              <input
                id="new-tenant-name"
                type="text"
                bind:value={newTenant.name}
                placeholder="e.g. ACME Corp"
                class="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                  bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                  focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            <div>
              <label for="new-tenant-quota-docs" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Doc Quota</label>
              <input
                id="new-tenant-quota-docs"
                type="number"
                bind:value={newTenant.quotaDocuments}
                class="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                  bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                  focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            <div>
              <label for="new-tenant-quota-queries" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Query Quota / Day</label>
              <input
                id="new-tenant-quota-queries"
                type="number"
                bind:value={newTenant.quotaQueriesPerDay}
                class="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                  bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                  focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
          </div>
          <button
            onclick={createTenant}
            disabled={creatingTenant || !newTenant.id.trim() || !newTenant.name.trim()}
            class="px-4 py-2 text-sm font-medium rounded-lg
              bg-blue-600 text-white hover:bg-blue-700
              disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {creatingTenant ? 'Creating...' : 'Create Tenant'}
          </button>
        </div>

        <!-- Tenant list with edit/delete -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h3 class="text-lg font-medium text-gray-900 dark:text-white">All Tenants</h3>
          </div>
          {#if tenants.length === 0}
            <div class="text-center py-8 text-sm text-gray-400">No tenants found.</div>
          {:else}
            <div class="divide-y divide-gray-100 dark:divide-gray-700">
              {#each tenants as tenant}
                <div class="p-4">
                  {#if editingTenantId === tenant.tenantId}
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                      <input
                        type="text"
                        bind:value={editTenant.name}
                        placeholder="Name"
                        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                          bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                      <input
                        type="number"
                        bind:value={editTenant.quotaDocuments}
                        placeholder="Doc quota"
                        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                          bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                      <input
                        type="number"
                        bind:value={editTenant.quotaQueriesPerDay}
                        placeholder="Query quota/day"
                        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                          bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                    </div>
                    <div class="flex gap-2">
                      <button
                        onclick={saveEditTenant}
                        disabled={savingTenant}
                        class="px-3 py-1 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        {savingTenant ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        onclick={() => editingTenantId = null}
                        class="px-3 py-1 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300"
                      >
                        Cancel
                      </button>
                    </div>
                  {:else}
                    <div class="flex items-center justify-between">
                      <div>
                        <p class="text-sm font-medium text-gray-900 dark:text-white">{tenant.name}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-400 font-mono">{tenant.tenantId} &middot; {tenant.documentCount ?? 0} docs, {tenant.queryCount ?? 0} queries</p>
                      </div>
                      <div class="flex gap-2">
                        <button
                          onclick={() => startEditTenant(tenant)}
                          class="px-2.5 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600
                            text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                        >
                          Edit
                        </button>
                        <button
                          onclick={() => confirmDeleteTenant(tenant.tenantId)}
                          disabled={deletingTenant}
                          class="px-2.5 py-1 text-xs rounded-lg text-red-600 dark:text-red-400
                            hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50 transition-colors"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}

    <!-- Users Tab -->
    {#if activeTab === 'users'}
      <div class="space-y-4">
        <!-- Tenant selector -->
        <div class="flex items-center gap-3">
          <select
            bind:value={selectedUserTenant}
            class="px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600
              bg-white dark:bg-gray-700 text-gray-900 dark:text-white
              focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
          >
            <option value="">Select a tenant...</option>
            {#each tenants as tenant}
              <option value={tenant.tenantId}>{tenant.name} ({tenant.tenantId})</option>
            {/each}
          </select>
          <button
            onclick={() => loadUsers()}
            disabled={!selectedUserTenant || usersLoading}
            class="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700
              disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {usersLoading ? 'Loading...' : 'Load Users'}
          </button>
        </div>

        {#if users.length > 0}
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <table class="w-full text-sm">
              <thead class="bg-gray-50 dark:bg-gray-700/50 border-b border-gray-200 dark:border-gray-700">
                <tr>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">User</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Email</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Tenant</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Role</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {#each users as user}
                  <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                    <td class="px-4 py-3">
                      <p class="font-medium text-gray-900 dark:text-white">{user.name || user.username}</p>
                      <p class="text-xs text-gray-400">@{user.username}</p>
                    </td>
                    <td class="px-4 py-3 text-gray-500 dark:text-gray-400">{user.email}</td>
                    <td class="px-4 py-3 font-mono text-xs text-gray-500 dark:text-gray-400">{user.tenantId}</td>
                    <td class="px-4 py-3">
                      <select
                        value={user.role}
                        onchange={(e) => updateUserRole(user, (e.target as HTMLSelectElement).value)}
                        disabled={updatingRoleFor === user.id}
                        class="text-xs rounded-lg border border-gray-300 dark:border-gray-600
                          bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1
                          disabled:opacity-50 focus:ring-1 focus:ring-blue-500"
                      >
                        <option value="tenant_user">tenant_user</option>
                        <option value="tenant_admin">tenant_admin</option>
                        <option value="platform_admin">platform_admin</option>
                      </select>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {:else if !usersLoading && selectedUserTenant}
          <p class="text-sm text-gray-400">No users found for this tenant. Ensure the Authentik token is configured.</p>
        {/if}
      </div>
    {/if}

    <!-- Model Tab -->
    {#if activeTab === 'model'}
      <div class="space-y-6">
        {#if modelLoading}
          <div class="text-center py-12 text-gray-400">Loading...</div>
        {:else}
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h3 class="text-base font-semibold text-gray-900 dark:text-white mb-1">Platform LLM Model</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">
              Set a platform-wide LLM model that overrides all tenant preferences.
              Select <span class="font-mono font-medium">Tenant Choice</span> to let each tenant pick their own.
            </p>
            <p class="text-xs text-amber-600 dark:text-amber-400 mb-4">
              Changing this setting will clear the semantic cache for ALL tenants.
            </p>

            <div class="flex items-end gap-3">
              <div class="flex-1">
                <label for="platform-model-select" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">
                  Global model override
                </label>
                <select
                  id="platform-model-select"
                  disabled={modelSaving}
                  bind:value={selectedPlatformModel}
                  class="w-full rounded-lg border border-gray-300 dark:border-gray-600
                    bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                    px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500
                    disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <option value={null}>Tenant Choice (no override)</option>
                  {#each availableModels as model}
                    <option value={model.name}>{model.name}</option>
                  {/each}
                </select>
              </div>
              <button
                onclick={() => requestPlatformModelChange(selectedPlatformModel)}
                disabled={modelSaving || selectedPlatformModel === (platformSettings?.llmModel ?? null)}
                class="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white
                  hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {modelSaving ? 'Saving...' : 'Apply'}
              </button>
            </div>

            <p class="text-xs text-gray-400 mt-3">
              Current setting:
              <span class="font-mono font-medium">
                {platformSettings?.llmModel ?? 'Tenant Choice'}
              </span>
            </p>
          </div>

          <!-- Per-tenant model overview -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h3 class="text-base font-semibold text-gray-900 dark:text-white mb-4">Per-Tenant Preferences</h3>
            {#if tenants.length === 0}
              <p class="text-sm text-gray-400">No tenants found.</p>
            {:else}
              <table class="w-full text-sm">
                <thead class="border-b border-gray-200 dark:border-gray-700">
                  <tr>
                    <th class="text-left py-2 font-medium text-gray-600 dark:text-gray-400">Tenant</th>
                    <th class="text-left py-2 font-medium text-gray-600 dark:text-gray-400">Preference</th>
                    <th class="text-left py-2 font-medium text-gray-600 dark:text-gray-400">Effective</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                  {#each tenants as tenant}
                    <tr>
                      <td class="py-2.5 font-mono text-xs text-gray-700 dark:text-gray-300">{tenant.tenantId}</td>
                      <td class="py-2.5 text-xs text-gray-500 dark:text-gray-400">
                        {tenant.settings?.llmModel ?? 'Not set'}
                      </td>
                      <td class="py-2.5 text-xs font-medium text-gray-900 dark:text-white">
                        {platformSettings?.llmModel ?? tenant.settings?.llmModel ?? 'Default'}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </div>
        {/if}
      </div>
    {/if}

  </div>
</div>

<ConfirmDialog
  open={confirmOpen}
  title={confirmMode === 'delete-tenant' ? 'Delete tenant?' : 'Delete all documents?'}
  message={confirmTenantId
    ? confirmMode === 'delete-tenant'
      ? `This will permanently delete tenant "${confirmTenantId}", ALL its documents, chunks, vectors, users, and query history. This cannot be undone.`
      : `This will permanently delete ALL documents, chunks, and vectors for tenant "${confirmTenantId}". This cannot be undone.`
    : ''}
  confirmLabel={confirmMode === 'delete-tenant' ? 'Delete Tenant' : 'Delete All'}
  dangerous={true}
  onconfirm={handleConfirm}
  oncancel={() => { confirmOpen = false; confirmTenantId = null; }}
/>

<ConfirmDialog
  open={platformModelConfirmOpen}
  title="Change platform model?"
  message={`This will set the platform model to "${pendingPlatformModel ?? 'Tenant Choice'}" and clear the semantic cache for ALL tenants. All cached responses will be regenerated. Proceed?`}
  confirmLabel="Apply & Clear All Caches"
  dangerous={false}
  onconfirm={confirmPlatformModelChange}
  oncancel={() => { platformModelConfirmOpen = false; pendingPlatformModel = undefined; }}
/>
