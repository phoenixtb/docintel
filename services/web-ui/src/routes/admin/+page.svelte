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
  let seedingTenantId: string | null = $state(null);

  async function seedTenantProfiles(tenantId: string) {
    seedingTenantId = tenantId;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles/seed`, { method: 'POST' });
      if (res.ok) {
        const seeded: any[] = await res.json();
        if (seeded.length === 0) toast.info(`${tenantId}: profiles already exist, nothing seeded`);
        else toast.success(`${tenantId}: seeded ${seeded.length} profile(s) from platform defaults`);
        await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles-cache`, { method: 'DELETE' });
      } else {
        toast.error(`Seed failed: ${res.status}`);
      }
    } catch (e) { toast.error(`Error: ${e}`); }
    seedingTenantId = null;
  }

  // Users
  interface TenantUser { id: string; email: string; username: string; name: string; role: string; tenantId: string }
  let users: TenantUser[] = $state([]);
  let usersLoading = $state(false);
  let selectedUserTenant = $state('');
  let updatingRoleFor: string | null = $state(null);

  // Platform model
  interface ModelInfo { name: string; size?: number; supports_thinking?: boolean }
  interface PlatformSettings { llmModel: string | null }
  let availableModels: ModelInfo[] = $state([]);
  let platformSettings: PlatformSettings | null = $state(null);
  let selectedPlatformModel: string | null = $state(null); // null = "Tenant Choice"
  let configuredDefaultModel: string | null = $state(null); // from .env via API
  let modelLoading = $state(false);
  let modelSaving = $state(false);
  let platformModelConfirmOpen = $state(false);
  let pendingPlatformModel: string | null | undefined = $state(undefined);

  // Model profiles
  interface ModelProfile {
    id: string; scope: string; tenantId: string | null; modelPattern: string;
    displayName: string | null;
    temperature: number | null; topP: number | null; maxTokens: number | null;
    frequencyPenalty: number | null; presencePenalty: number | null; repetitionPenalty: number | null;
    topK: number | null; minP: number | null;
    thinkingTemperature: number | null; thinkingTopP: number | null; thinkingMaxTokens: number | null;
    thinkingFrequencyPenalty: number | null; thinkingPresencePenalty: number | null; thinkingRepetitionPenalty: number | null;
    thinkingTopK: number | null; thinkingMinP: number | null;
    thinkingBudget: number | null; streamThinking: boolean | null;
    notes: string | null;
  }
  const EMPTY_PROFILE_FORM = () => ({
    modelPattern: '', displayName: '',
    temperature: '', topP: '', maxTokens: '',
    frequencyPenalty: '', presencePenalty: '', repetitionPenalty: '', topK: '', minP: '',
    thinkingTemperature: '', thinkingTopP: '', thinkingMaxTokens: '',
    thinkingFrequencyPenalty: '', thinkingPresencePenalty: '', thinkingRepetitionPenalty: '', thinkingTopK: '', thinkingMinP: '',
    thinkingBudget: '', streamThinking: null as boolean | null,
    notes: '',
  });
  const BUILTIN_PROFILES = [
    { pattern: 'qwen3*', temp: 0.1, thinkTemp: 0.6, thinkTopP: 0.95, thinkMaxTokens: 6144, thinkBudget: 4096, streamThink: true },
    { pattern: 'qwq*', temp: 0.6, thinkTemp: 0.7, thinkTopP: 0.95, thinkMaxTokens: 6144, thinkBudget: 4096, streamThink: true },
    { pattern: 'deepseek-r1*', temp: 0.6, thinkTemp: 0.7, thinkTopP: 0.95, thinkMaxTokens: 6144, thinkBudget: null, streamThink: true },
    { pattern: 'marco-o1*', temp: 0.7, thinkTemp: 0.7, thinkTopP: 0.95, thinkMaxTokens: 6144, thinkBudget: 4096, streamThink: true },
  ];
  let platformProfiles: ModelProfile[] = $state([]);
  let profileModalOpen = $state(false);
  let profileModalMode: 'create' | 'edit' = $state('create');
  let editingProfileId: string | null = $state(null);
  let profileForm = $state(EMPTY_PROFILE_FORM());
  let profileSaving = $state(false);
  let profileDeleteConfirmId: string | null = $state(null);

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
      const [modelsRes, settingsRes, profilesRes] = await Promise.all([
        apiFetch('/api/v1/models'),
        apiFetch('/api/v1/admin/platform/settings'),
        apiFetch('/api/v1/admin/model-profiles'),
      ]);
      if (modelsRes.ok) {
        const mdata = await modelsRes.json();
        availableModels = mdata.models ?? [];
        configuredDefaultModel = mdata.default_model ?? null;
      }
      if (settingsRes.ok) {
        platformSettings = await settingsRes.json();
        selectedPlatformModel = platformSettings?.llmModel ?? null;
      }
      if (profilesRes.ok) platformProfiles = await profilesRes.json();
    } catch (e) { toast.error(`Failed to load model settings: ${e}`); }
    modelLoading = false;
  }

  function openCreateProfileModal() {
    profileModalMode = 'create';
    editingProfileId = null;
    profileForm = EMPTY_PROFILE_FORM();
    profileModalOpen = true;
  }

  function openEditProfileModal(p: ModelProfile) {
    profileModalMode = 'edit';
    editingProfileId = p.id;
    const s = (v: number | null) => v != null ? String(v) : '';
    profileForm = {
      modelPattern: p.modelPattern,
      displayName: p.displayName ?? '',
      temperature: s(p.temperature),
      topP: s(p.topP),
      maxTokens: s(p.maxTokens),
      frequencyPenalty: s(p.frequencyPenalty),
      presencePenalty: s(p.presencePenalty),
      repetitionPenalty: s(p.repetitionPenalty),
      topK: s(p.topK),
      minP: s(p.minP),
      thinkingTemperature: s(p.thinkingTemperature),
      thinkingTopP: s(p.thinkingTopP),
      thinkingMaxTokens: s(p.thinkingMaxTokens),
      thinkingFrequencyPenalty: s(p.thinkingFrequencyPenalty),
      thinkingPresencePenalty: s(p.thinkingPresencePenalty),
      thinkingRepetitionPenalty: s(p.thinkingRepetitionPenalty),
      thinkingTopK: s(p.thinkingTopK),
      thinkingMinP: s(p.thinkingMinP),
      thinkingBudget: s(p.thinkingBudget),
      streamThinking: p.streamThinking,
      notes: p.notes ?? '',
    };
    profileModalOpen = true;
  }

  function profileFormBody() {
    const num = (v: string | number) => typeof v === 'number' ? (isNaN(v) ? null : v) : (v.trim() === '' ? null : Number(v));
    const int = (v: string | number) => typeof v === 'number' ? (isNaN(v) ? null : Math.round(v)) : (v.trim() === '' ? null : parseInt(v));
    return {
      modelPattern: profileForm.modelPattern.trim(),
      displayName: profileForm.displayName.trim() || null,
      temperature: num(profileForm.temperature),
      topP: num(profileForm.topP),
      maxTokens: int(profileForm.maxTokens),
      frequencyPenalty: num(profileForm.frequencyPenalty),
      presencePenalty: num(profileForm.presencePenalty),
      repetitionPenalty: num(profileForm.repetitionPenalty),
      topK: int(profileForm.topK),
      minP: num(profileForm.minP),
      thinkingTemperature: num(profileForm.thinkingTemperature),
      thinkingTopP: num(profileForm.thinkingTopP),
      thinkingMaxTokens: int(profileForm.thinkingMaxTokens),
      thinkingFrequencyPenalty: num(profileForm.thinkingFrequencyPenalty),
      thinkingPresencePenalty: num(profileForm.thinkingPresencePenalty),
      thinkingRepetitionPenalty: num(profileForm.thinkingRepetitionPenalty),
      thinkingTopK: int(profileForm.thinkingTopK),
      thinkingMinP: num(profileForm.thinkingMinP),
      thinkingBudget: int(profileForm.thinkingBudget),
      streamThinking: profileForm.streamThinking,
      notes: profileForm.notes.trim() || null,
    };
  }

  async function saveProfile() {
    if (!profileForm.modelPattern.trim()) { toast.error('Model pattern is required'); return; }
    profileSaving = true;
    try {
      const url = profileModalMode === 'create'
        ? '/api/v1/admin/model-profiles'
        : `/api/v1/admin/model-profiles/${editingProfileId}`;
      const method = profileModalMode === 'create' ? 'POST' : 'PUT';
      const res = await apiFetch(url, { method, headers: jsonHeaders(), body: JSON.stringify(profileFormBody()) });
      if (!res.ok) throw new Error(`${res.status}`);
      await apiFetch('/api/v1/admin/model-profiles-cache', { method: 'DELETE' });
      toast.success(profileModalMode === 'create' ? 'Profile created' : 'Profile updated');
      profileModalOpen = false;
      const prRes = await apiFetch('/api/v1/admin/model-profiles');
      if (prRes.ok) platformProfiles = await prRes.json();
    } catch (e) { toast.error(`Failed: ${e}`); }
    profileSaving = false;
  }

  async function deleteProfile(id: string) {
    profileDeleteConfirmId = null;
    try {
      const res = await apiFetch(`/api/v1/admin/model-profiles/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`${res.status}`);
      await apiFetch('/api/v1/admin/model-profiles-cache', { method: 'DELETE' });
      toast.success('Profile deleted');
      platformProfiles = platformProfiles.filter(p => p.id !== id);
    } catch (e) { toast.error(`Failed: ${e}`); }
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
                          onclick={() => seedTenantProfiles(tenant.tenantId)}
                          disabled={seedingTenantId === tenant.tenantId}
                          title="Copy platform model profiles to this tenant (skips existing)"
                          class="px-2.5 py-1 text-xs rounded-lg border border-emerald-300 dark:border-emerald-700
                            text-emerald-700 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-900/20
                            disabled:opacity-50 transition-colors"
                        >
                          {seedingTenantId === tenant.tenantId ? 'Seeding...' : 'Seed Profiles'}
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
                  <option value={null}>Tenant Choice{configuredDefaultModel ? ` — using: ${configuredDefaultModel}` : ' (no override)'}</option>
                  {#each availableModels as model}
                    <option value={model.name}>{model.name}{model.supports_thinking ? ' ✦' : ''}</option>
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

          <!-- Platform Model Profiles -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div class="flex items-center justify-between mb-1">
              <h3 class="text-base font-semibold text-gray-900 dark:text-white">Platform Model Profiles</h3>
              <button
                onclick={openCreateProfileModal}
                class="px-3 py-1.5 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
              >+ Add Profile</button>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">
              Override sampling params (temperature, top_p, max_tokens…) per model pattern.
              Patterns: exact name or prefix wildcard (<span class="font-mono">qwen3*</span>, <span class="font-mono">deepseek-r1:7b</span>).
              Blank = inherit from built-in defaults or env config.
            </p>
            {#if platformProfiles.length === 0}
              <p class="text-sm text-gray-400">No platform profiles defined — built-in defaults apply.</p>
            {:else}
              <div class="overflow-x-auto">
                <table class="w-full text-xs">
                  <thead class="border-b border-gray-200 dark:border-gray-700">
                    <tr>
                      <th class="text-left py-2 font-medium text-gray-600 dark:text-gray-400 pr-4">Pattern</th>
                      <th class="text-left py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Display</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Temp</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think Temp</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think TopP</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think MaxTok</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think Budget</th>
                      <th class="text-center py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Stream ∵</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think RepPen</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think FreqPen</th>
                      <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">MaxTok</th>
                      <th class="py-2"></th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                    {#each platformProfiles as p}
                      <tr>
                        <td class="py-2 font-mono text-gray-900 dark:text-white pr-4">{p.modelPattern}</td>
                        <td class="py-2 text-gray-500 dark:text-gray-400 pr-3">{p.displayName ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.temperature ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingTemperature ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingTopP ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingMaxTokens ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingBudget ?? '—'}</td>
                        <td class="py-2 text-center pr-3">{p.streamThinking === true ? '✓' : p.streamThinking === false ? '✗' : '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingRepetitionPenalty ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingFrequencyPenalty ?? '—'}</td>
                        <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.maxTokens ?? '—'}</td>
                        <td class="py-2 text-right whitespace-nowrap">
                          <button onclick={() => openEditProfileModal(p)} class="text-blue-600 hover:text-blue-800 dark:text-blue-400 mr-2">Edit</button>
                          {#if profileDeleteConfirmId === p.id}
                            <button onclick={() => deleteProfile(p.id)} class="text-red-600 hover:text-red-800 font-medium mr-1">Confirm</button>
                            <button onclick={() => profileDeleteConfirmId = null} class="text-gray-400 hover:text-gray-600">Cancel</button>
                          {:else}
                            <button onclick={() => profileDeleteConfirmId = p.id} class="text-red-400 hover:text-red-600">Delete</button>
                          {/if}
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          </div>

          <!-- Built-in Defaults reference card -->
          <div class="bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 p-6">
            <h3 class="text-sm font-semibold text-gray-600 dark:text-gray-400 mb-3">Built-in Defaults (read-only, below DB profiles)</h3>
            <div class="overflow-x-auto">
              <table class="w-full text-xs">
                <thead class="border-b border-gray-200 dark:border-gray-700">
                  <tr>
                    <th class="text-left py-1.5 font-medium text-gray-500 pr-4">Pattern</th>
                    <th class="text-right py-1.5 font-medium text-gray-500 pr-3">Temp</th>
                    <th class="text-right py-1.5 font-medium text-gray-500 pr-3">Think Temp</th>
                    <th class="text-right py-1.5 font-medium text-gray-500 pr-3">Think TopP</th>
                    <th class="text-right py-1.5 font-medium text-gray-500 pr-3">Think MaxTok</th>
                    <th class="text-right py-1.5 font-medium text-gray-500 pr-3">Budget</th>
                    <th class="text-center py-1.5 font-medium text-gray-500">Stream ∵</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                  {#each BUILTIN_PROFILES as bp}
                    <tr>
                      <td class="py-1.5 font-mono text-gray-600 dark:text-gray-400 pr-4">{bp.pattern}</td>
                      <td class="py-1.5 text-right text-gray-500 pr-3">{bp.temp}</td>
                      <td class="py-1.5 text-right text-gray-500 pr-3">{bp.thinkTemp}</td>
                      <td class="py-1.5 text-right text-gray-500 pr-3">{bp.thinkTopP}</td>
                      <td class="py-1.5 text-right text-gray-500 pr-3">{bp.thinkMaxTokens}</td>
                      <td class="py-1.5 text-right text-gray-500 pr-3">{bp.thinkBudget ?? '—'}</td>
                      <td class="py-1.5 text-center text-gray-500">{bp.streamThink ? '✓' : '✗'}</td>
                    </tr>
                  {/each}
                  <tr class="italic">
                    <td class="py-1.5 font-mono text-gray-400 pr-4">* (catch-all)</td>
                    <td class="py-1.5 text-right text-gray-400 pr-3" colspan="4">all null → env config fallback</td>
                  </tr>
                </tbody>
              </table>
            </div>
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

{#if profileModalOpen}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onclick={() => { profileModalOpen = false; }}>
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
    <div class="bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6 space-y-4" onclick={(e) => e.stopPropagation()}>
      <h2 class="text-lg font-semibold text-gray-900 dark:text-white">
        {profileModalMode === 'create' ? 'Add Platform Profile' : 'Edit Platform Profile'}
      </h2>

      <div class="grid grid-cols-2 gap-3">
        <div class="col-span-2">
          <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
            Model Pattern <span class="text-red-500">*</span>
            <span class="ml-1 text-gray-400">(e.g. <span class="font-mono">qwen3*</span> or <span class="font-mono">qwen3:8b</span>)</span>
          </label>
          <input bind:value={profileForm.modelPattern} placeholder="qwen3*"
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500" />
        </div>
        <div class="col-span-2">
          <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Display Name</label>
          <input bind:value={profileForm.displayName} placeholder="Qwen3 family"
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500" />
        </div>

        <!-- Two-column layout: Standard | Thinking -->
        <div class="col-span-2 grid grid-cols-2 gap-x-6 gap-y-3 mt-1">
          <!-- Column headers -->
          <p class="text-xs text-gray-500 dark:text-gray-400 font-semibold border-b border-gray-200 dark:border-gray-700 pb-1">Standard mode</p>
          <p class="text-xs text-gray-500 dark:text-gray-400 font-semibold border-b border-gray-200 dark:border-gray-700 pb-1">Thinking mode</p>

          <!-- Temperature row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Temperature <span class="text-gray-400">(blank = inherit)</span></label>
            <input type="number" step="0.01" min="0" max="2" bind:value={profileForm.temperature} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Temperature</label>
            <input type="number" step="0.01" min="0" max="2" bind:value={profileForm.thinkingTemperature} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Top P row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Top P</label>
            <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.topP} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Top P</label>
            <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.thinkingTopP} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Top K row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Top K</label>
            <input type="number" step="1" min="0" bind:value={profileForm.topK} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Top K</label>
            <input type="number" step="1" min="0" bind:value={profileForm.thinkingTopK} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Min P row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Min P</label>
            <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.minP} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Min P</label>
            <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.thinkingMinP} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Frequency Penalty row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Frequency Penalty</label>
            <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.frequencyPenalty} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Frequency Penalty</label>
            <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.thinkingFrequencyPenalty} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Presence Penalty row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Presence Penalty</label>
            <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.presencePenalty} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Presence Penalty</label>
            <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.thinkingPresencePenalty} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Repetition Penalty row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Repetition Penalty</label>
            <input type="number" step="0.01" min="1" max="2" bind:value={profileForm.repetitionPenalty} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Repetition Penalty</label>
            <input type="number" step="0.01" min="1" max="2" bind:value={profileForm.thinkingRepetitionPenalty} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Max Tokens row -->
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Max Tokens</label>
            <input type="number" step="1" min="1" bind:value={profileForm.maxTokens} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Max Tokens</label>
            <input type="number" step="1" min="1" bind:value={profileForm.thinkingMaxTokens} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <!-- Budget + Stream row (thinking-only) -->
          <div></div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Budget <span class="text-gray-400">(LMForge cap)</span></label>
            <input type="number" step="1" min="1" bind:value={profileForm.thinkingBudget} placeholder=""
              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
          </div>

          <div></div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-500 dark:text-gray-400 flex-1">
              Stream reasoning tokens
              <span class="text-gray-400 ml-1">(stream_reasoning_deltas)</span>
            </label>
            <select bind:value={profileForm.streamThinking}
              class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-2 py-1.5 text-sm">
              <option value={null}>Inherit</option>
              <option value={true}>Enabled</option>
              <option value={false}>Disabled</option>
            </select>
          </div>
        </div>

        <div class="col-span-2">
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Notes</label>
          <textarea bind:value={profileForm.notes} rows="2" placeholder="Optional notes"
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm resize-none"></textarea>
        </div>
      </div>

      <div class="flex justify-end gap-3 pt-2">
        <button onclick={() => profileModalOpen = false}
          class="px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
          Cancel
        </button>
        <button onclick={saveProfile} disabled={profileSaving}
          class="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed">
          {profileSaving ? 'Saving...' : profileModalMode === 'create' ? 'Create' : 'Save'}
        </button>
      </div>
    </div>
  </div>
{/if}
