<script lang="ts">
  import { onMount } from 'svelte';
  import { getTenantId, getAuthState, isTenantAdmin } from '$lib/auth';
  import { apiFetch } from '$lib/api';
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

  let activeTab: 'usage' | 'documents' | 'users' | 'model' = $state('usage');
  let loading = $state(true);

  // Usage
  let usage: any = $state(null);

  // Documents
  interface Doc {
    id: string;
    filename: string;
    fileSize: number;
    chunkCount: number;
    status: string;
    metadata: Record<string, string>;
    createdAt: string;
  }
  let documents: Doc[] = $state([]);
  let docsLoading = $state(false);
  let confirmOpen = $state(false);
  let confirmDocId: string | null = $state(null);
  let confirmDocName = $state('');
  let deleting = $state(false);

  // Users
  interface TenantUser { id: string; email: string; username: string; name: string; role: string; tenantId: string }
  let users: TenantUser[] = $state([]);
  let usersLoading = $state(false);
  let updatingRoleFor: string | null = $state(null);

  // Model
  interface ModelInfo { name: string; size?: number; modified_at?: string }
  interface TenantSettings { llmModel: string | null; effectiveModel: string | null }
  let modelLoading = $state(false);
  let availableModels: ModelInfo[] = $state([]);
  let tenantSettings: TenantSettings | null = $state(null);
  let selectedModel: string | null = $state(null);   // null = "Platform Default"
  let modelSaving = $state(false);
  let modelConfirmOpen = $state(false);
  let pendingModel: string | null = $state(null);

  let isPlatformControlled = $derived(
    tenantSettings != null &&
    (tenantSettings as TenantSettings).llmModel === null &&
    (tenantSettings as TenantSettings).effectiveModel !== null
  );

  const tenantId = getTenantId();
  const jsonHeaders = () => ({ 'Content-Type': 'application/json' });

  async function fetchJson(url: string) {
    const res = await apiFetch(url);
    return res.ok ? res.json() : null;
  }

  async function loadUsage() {
    loading = true;
    usage = await fetchJson(`/api/v1/tenants/${tenantId}/usage`);
    loading = false;
  }

  async function loadDocuments() {
    docsLoading = true;
    const data = await fetchJson(`/api/v1/documents?page=0&size=100`);
    documents = data?.content ?? data ?? [];
    docsLoading = false;
  }

  async function loadUsers() {
    usersLoading = true;
    users = (await fetchJson(`/api/v1/tenants/${tenantId}/users`)) ?? [];
    usersLoading = false;
  }

  function confirmDeleteDoc(doc: Doc) {
    confirmDocId = doc.id;
    confirmDocName = doc.filename;
    confirmOpen = true;
  }

  async function doDeleteDoc() {
    if (!confirmDocId) return;
    confirmOpen = false;
    deleting = true;
    try {
      const res = await apiFetch(`/api/v1/documents/${confirmDocId}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success(`Deleted "${confirmDocName}"`);
        await loadDocuments();
        await loadUsage();
      } else {
        toast.error(`Failed to delete: ${res.status}`);
      }
    } catch (e) {
      toast.error(`Error: ${e}`);
    }
    deleting = false;
    confirmDocId = null;
  }

  async function updateRole(user: TenantUser, newRole: string) {
    updatingRoleFor = user.id;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/users/${user.id}/role`, {
        method: 'PUT',
        headers: jsonHeaders(),
        body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) {
        toast.success(`Updated role for ${user.username}`);
        await loadUsers();
      } else {
        toast.error(`Failed to update role: ${res.status}`);
      }
    } catch (e) {
      toast.error(`Error: ${e}`);
    }
    updatingRoleFor = null;
  }

  async function loadModel() {
    modelLoading = true;
    try {
      const [modelsRes, settingsRes] = await Promise.all([
        apiFetch('/api/v1/models'),
        apiFetch(`/api/v1/tenants/${tenantId}/settings`),
      ]);
      if (modelsRes.ok) {
        const data = await modelsRes.json();
        availableModels = data.models ?? [];
      }
      if (settingsRes.ok) {
        tenantSettings = await settingsRes.json();
        selectedModel = tenantSettings?.llmModel ?? null;
      }
    } catch (e) {
      toast.error(`Failed to load model settings: ${e}`);
    }
    modelLoading = false;
  }

  function requestModelChange(model: string | null) {
    pendingModel = model;
    modelConfirmOpen = true;
  }

  async function confirmModelChange() {
    modelConfirmOpen = false;
    modelSaving = true;
    try {
      // 1. Update tenant model preference
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/settings`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ llmModel: pendingModel }),
      });
      if (!res.ok) throw new Error(`${res.status}`);

      // 2. Invalidate semantic cache for this tenant
      await apiFetch(`/api/v1/admin/cache/clear/${tenantId}`, { method: 'POST' });

      selectedModel = pendingModel;
      tenantSettings = await res.json();
      toast.success('Model updated and cache cleared.');
    } catch (e) {
      toast.error(`Failed to update model: ${e}`);
    }
    modelSaving = false;
    pendingModel = null;
  }

  function switchTab(tab: typeof activeTab) {
    activeTab = tab;
    if (tab === 'usage') loadUsage();
    if (tab === 'documents') loadDocuments();
    if (tab === 'users') loadUsers();
    if (tab === 'model') loadModel();
  }

  onMount(() => {
    loadUsage();
  });

  function formatBytes(bytes: number) {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  }

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString();
  }

  function getDomainColor(domain: string): string {
    const colors: Record<string, string> = {
      technical: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
      hr_policy: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
      contracts: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
      general: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
    };
    return colors[domain] || colors.general;
  }

  const DOMAIN_LABELS: Record<string, string> = {
    technical: 'Technical',
    hr_policy: 'HR Policy',
    contracts: 'Contracts',
    general: 'General',
  };
</script>

<div class="h-full overflow-y-auto">
  <div class="max-w-5xl mx-auto p-6">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">Manage your tenant: <span class="font-mono font-medium">{tenantId}</span></p>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700">
      {#each [
        { id: 'usage', label: 'Usage' },
        { id: 'documents', label: 'Documents' },
        { id: 'users', label: 'Users' },
        ...(isTenantAdmin() ? [{ id: 'model', label: 'Model' }] : []),
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

    <!-- Usage Tab -->
    {#if activeTab === 'usage'}
      {#if loading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else if usage}
        <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
          {#each [
            { label: 'Documents', value: usage.documentCount ?? 0 },
            { label: 'Chunks', value: usage.chunkCount ?? 0 },
            { label: 'Total Queries', value: usage.totalQueries ?? 0 },
            { label: 'Queries (24h)', value: usage.queriesLast24h ?? 0 },
            { label: 'Cache Hit Rate', value: usage.cacheHitRate != null ? `${(usage.cacheHitRate * 100).toFixed(1)}%` : 'N/A' },
            { label: 'Storage', value: formatBytes(usage.storageBytes ?? 0) },
          ] as item}
            <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <p class="text-sm text-gray-500 dark:text-gray-400">{item.label}</p>
              <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{item.value}</p>
            </div>
          {/each}
        </div>
        {#if usage.lastQueryAt}
          <p class="text-xs text-gray-400 mt-4">Last query: {new Date(usage.lastQueryAt).toLocaleString()}</p>
        {/if}
      {:else}
        <p class="text-sm text-gray-400">No usage data available.</p>
      {/if}
    {/if}

    <!-- Documents Tab -->
    {#if activeTab === 'documents'}
      {#if docsLoading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else}
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {#if documents.length === 0}
            <div class="text-center py-12 text-gray-400 text-sm">No documents found.</div>
          {:else}
            <table class="w-full text-sm">
              <thead class="bg-gray-50 dark:bg-gray-700/50 border-b border-gray-200 dark:border-gray-700">
                <tr>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">File</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Type</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Source</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Status</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Chunks</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Uploaded</th>
                  <th class="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {#each documents as doc}
                  <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                    <td class="px-4 py-3 font-medium text-gray-900 dark:text-white truncate max-w-xs">{doc.filename}</td>
                    <td class="px-4 py-3">
                      {#if doc.metadata?.domain}
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {getDomainColor(doc.metadata.domain)}">
                          {DOMAIN_LABELS[doc.metadata.domain] || doc.metadata.domain}
                        </span>
                      {:else}
                        <span class="text-gray-400 text-xs">—</span>
                      {/if}
                    </td>
                    <td class="px-4 py-3">
                      {#if doc.metadata?.source === 'sample_dataset'}
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400">
                          Sample
                        </span>
                      {:else}
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400">
                          Uploaded
                        </span>
                      {/if}
                    </td>
                    <td class="px-4 py-3">
                      <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium
                        {doc.status === 'COMPLETED' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' :
                         doc.status === 'FAILED' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' :
                         'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'}">
                        {doc.status}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-gray-500 dark:text-gray-400">{doc.chunkCount}</td>
                    <td class="px-4 py-3 text-gray-500 dark:text-gray-400">{formatDate(doc.createdAt)}</td>
                    <td class="px-4 py-3 text-right">
                      <button
                        onclick={() => confirmDeleteDoc(doc)}
                        disabled={deleting}
                        class="px-2.5 py-1 text-xs rounded-lg text-red-600 dark:text-red-400
                          hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50 transition-colors"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {/if}
    {/if}

    <!-- Users Tab -->
    {#if activeTab === 'users'}
      {#if usersLoading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else}
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {#if users.length === 0}
            <div class="text-center py-12 text-sm">
              <p class="text-gray-400">No users found.</p>
              <p class="text-gray-500 dark:text-gray-500 text-xs mt-1">Users are managed via Authentik. Ensure the Authentik token is configured.</p>
            </div>
          {:else}
            <table class="w-full text-sm">
              <thead class="bg-gray-50 dark:bg-gray-700/50 border-b border-gray-200 dark:border-gray-700">
                <tr>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">User</th>
                  <th class="text-left px-4 py-3 font-medium text-gray-700 dark:text-gray-300">Email</th>
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
                    <td class="px-4 py-3">
                      <select
                        value={user.role}
                        onchange={(e) => updateRole(user, (e.target as HTMLSelectElement).value)}
                        disabled={updatingRoleFor === user.id}
                        class="text-xs rounded-lg border border-gray-300 dark:border-gray-600
                          bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1
                          disabled:opacity-50 focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="tenant_user">tenant_user</option>
                        <option value="tenant_admin">tenant_admin</option>
                      </select>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {/if}
    {/if}

    <!-- Model Tab -->
    {#if activeTab === 'model'}
      {#if modelLoading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else}
        <div class="space-y-6">

          <!-- Platform override notice -->
          {#if tenantSettings?.effectiveModel && tenantSettings.llmModel === null && tenantSettings.effectiveModel !== null}
            <div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 rounded-lg px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
              <span class="font-semibold">Platform controlled:</span> The platform administrator has set a global model override.
              Your tenant is currently using <span class="font-mono font-medium">{tenantSettings.effectiveModel}</span>.
              Contact your platform administrator to change this.
            </div>
          {/if}

          <!-- Model selector -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h3 class="text-base font-semibold text-gray-900 dark:text-white mb-1">LLM Model</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Select the language model used for answering queries in your tenant.
              Changing the model will invalidate the semantic cache — cached responses will be regenerated.
            </p>

            <div class="flex items-end gap-3">
              <div class="flex-1">
                <label for="model-select" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">
                  Model
                </label>
                <select
                  id="model-select"
                  disabled={isPlatformControlled || modelSaving}
                  bind:value={selectedModel}
                  class="w-full rounded-lg border border-gray-300 dark:border-gray-600
                    bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                    px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500
                    disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <option value={null}>Platform Default</option>
                  {#each availableModels as model}
                    <option value={model.name}>{model.name}</option>
                  {/each}
                </select>
              </div>
              <button
                onclick={() => requestModelChange(selectedModel)}
                disabled={isPlatformControlled || modelSaving || selectedModel === (tenantSettings?.llmModel ?? null)}
                class="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white
                  hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {modelSaving ? 'Saving...' : 'Save'}
              </button>
            </div>

            {#if tenantSettings?.effectiveModel}
              <p class="text-xs text-gray-400 mt-3">
                Currently active: <span class="font-mono">{tenantSettings.effectiveModel}</span>
              </p>
            {/if}
          </div>
        </div>
      {/if}
    {/if}
  </div>
</div>

<ConfirmDialog
  open={confirmOpen}
  title="Delete document?"
  message={`This will permanently delete "${confirmDocName}" and all its chunks and vectors. This cannot be undone.`}
  confirmLabel="Delete"
  dangerous={true}
  onconfirm={doDeleteDoc}
  oncancel={() => { confirmOpen = false; confirmDocId = null; }}
/>

<ConfirmDialog
  open={modelConfirmOpen}
  title="Change LLM model?"
  message={`This will update the active model to "${pendingModel ?? 'Platform Default'}" and clear the semantic cache for your tenant. Cached responses will be regenerated on the next query. Proceed?`}
  confirmLabel="Change & Clear Cache"
  dangerous={false}
  onconfirm={confirmModelChange}
  oncancel={() => { modelConfirmOpen = false; pendingModel = null; }}
/>
