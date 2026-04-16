<script lang="ts">
  import { onMount } from 'svelte';
  import { getTenantId, getAuthState, isTenantAdmin } from '$lib/auth';
  import { apiFetch } from '$lib/api';
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

  let activeTab: 'usage' | 'documents' | 'users' | 'model' | 'cache' = $state('usage');
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

  // Cleanup
  interface CleanupJobState {
    jobId: string; tenantId: string; status: string;
    total: number; processed: number; succeeded: number; failed: number;
    errors?: string[];
  }
  let cleanupFiltersOpen = $state(false);
  let cleanupFilters = $state({
    statuses: [] as string[],
    createdAfter: '',
    createdBefore: '',
    domain: '',
    contentType: '',
    uploadOrigin: '' as '' | 'MANUAL' | 'DATA_SOURCE',
    metadataSource: '',
  });
  let cleanupPreview: { matchCount: number } | null = $state(null);
  let cleanupPreviewing = $state(false);
  let cleanupConfirmOpen = $state(false);
  let cleanupJob: CleanupJobState | null = $state(null);
  let cleanupStarting = $state(false);
  let cleanupSseAbort: AbortController | null = null;

  // Users
  interface TenantUser { id: string; email: string; username: string; name: string; role: string; tenantId: string }
  let users: TenantUser[] = $state([]);
  let usersLoading = $state(false);
  let updatingRoleFor: string | null = $state(null);

  // Model
  interface ModelInfo { name: string; size?: number; modified_at?: string; supports_thinking?: boolean }
  interface TenantSettings { llmModel: string | null; effectiveModel: string | null; thinkingMode: boolean }
  let modelLoading = $state(false);
  let availableModels: ModelInfo[] = $state([]);
  let platformDefaultModel: string | null = $state(null);
  let tenantSettings: TenantSettings | null = $state(null);
  let selectedModel: string | null = $state(null);   // null = "Platform Default"
  let thinkingMode = $state(false);
  let thinkingSaving = $state(false);
  let modelSaving = $state(false);
  let modelConfirmOpen = $state(false);
  let pendingModel: string | null = $state(null);

  // Cache
  interface CacheStats { totalEntries: number; hitRate: number; avgLatencySavedMs: number }
  let cacheLoading = $state(false);
  let cacheStats: CacheStats | null = $state(null);
  let clearingSemanticCache = $state(false);
  let clearingResolverCache = $state(false);
  let semanticCacheConfirmOpen = $state(false);

  // Platform admin has a global override when effectiveModel differs from tenant's own llmModel
  let isPlatformControlled = $derived(
    tenantSettings != null &&
    tenantSettings.effectiveModel !== null &&
    tenantSettings.effectiveModel !== tenantSettings.llmModel
  );

  // Whether the currently selected/active model supports thinking
  let effectiveModelName = $derived(tenantSettings?.effectiveModel ?? selectedModel ?? platformDefaultModel ?? '');
  let activeModelSupportsThinking = $derived(
    availableModels.find(m => m.name === effectiveModelName)?.supports_thinking ?? false
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

  function buildCleanupBody() {
    const body: Record<string, unknown> = {};
    if (cleanupFilters.statuses.length) body.statuses = cleanupFilters.statuses;
    if (cleanupFilters.createdAfter) body.createdAfter = new Date(cleanupFilters.createdAfter).toISOString();
    if (cleanupFilters.createdBefore) body.createdBefore = new Date(cleanupFilters.createdBefore).toISOString();
    if (cleanupFilters.domain) body.domain = cleanupFilters.domain;
    if (cleanupFilters.contentType) body.contentType = cleanupFilters.contentType;
    if (cleanupFilters.uploadOrigin) body.uploadOrigin = cleanupFilters.uploadOrigin;
    if (cleanupFilters.metadataSource) body.metadataSource = cleanupFilters.metadataSource;
    return body;
  }

  async function previewCleanup() {
    cleanupPreviewing = true;
    cleanupPreview = null;
    try {
      const res = await apiFetch('/api/v1/documents/cleanup/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildCleanupBody()),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      cleanupPreview = await res.json();
    } catch (e) {
      toast.error(`Preview failed: ${e}`);
    }
    cleanupPreviewing = false;
  }

  async function startCleanup() {
    cleanupConfirmOpen = false;
    cleanupStarting = true;
    try {
      const res = await apiFetch('/api/v1/documents/cleanup/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildCleanupBody()),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error ?? `${res.status}`);
      }
      const job = await res.json();
      cleanupJob = { jobId: job.jobId, tenantId: job.tenantId, status: job.status, total: job.matchCount, processed: 0, succeeded: 0, failed: 0 };
      cleanupPreview = null;
      runCleanupSse(job.jobId);
    } catch (e) {
      toast.error(`Failed to start cleanup: ${e}`);
    }
    cleanupStarting = false;
  }

  // SSE uses fetch (not EventSource) so apiFetch can inject Authorization: Bearer.
  // EventSource does not support custom headers — the token lives in localStorage, not a cookie.
  function runCleanupSse(jobId: string) {
    cleanupSseAbort?.abort();
    cleanupSseAbort = new AbortController();
    const signal = cleanupSseAbort.signal;

    (async () => {
      try {
        while (!signal.aborted) {
          try {
            const res = await apiFetch(`/api/v1/documents/cleanup/jobs/${jobId}/events`, {
              signal,
              headers: { Accept: 'text/event-stream' },
            });

            if (!res.ok || !res.body) {
              await new Promise(r => setTimeout(r, 3000));
              continue;
            }

            const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
            let buf = '';
            while (true) {
              const { value, done } = await reader.read();
              if (done) break;
              buf += value.replace(/\r\n/g, '\n');
              const parts = buf.split('\n\n');
              buf = parts.pop() ?? '';
              for (const block of parts) {
                if (!block.trim() || block.startsWith(':')) continue;
                let eventType = 'message', dataLine = '';
                for (const rawLine of block.split('\n')) {
                  const line = rawLine.trimEnd();
                  if (line.startsWith('event:')) eventType = line.slice(6).trimStart();
                  if (line.startsWith('data:'))  dataLine  = line.slice(5).trimStart();
                }
                if (!dataLine) continue;
                const data = JSON.parse(dataLine);
                if (eventType === 'cleanup_progress') {
                  if (cleanupJob) cleanupJob = { ...cleanupJob, ...data };
                } else if (eventType === 'cleanup_complete') {
                  if (cleanupJob) cleanupJob = { ...cleanupJob, ...data };
                  closeCleanupSse();
                  loadDocuments();
                  loadUsage();
                  if (data.status === 'COMPLETED') toast.success(`Cleanup done: ${data.succeeded} deleted.`);
                  else if (data.status === 'CANCELLED') toast.info('Cleanup cancelled.');
                  else toast.error(`Cleanup finished with errors: ${data.failed} failed.`);
                  return;
                }
              }
            }

            // Stream closed — stop if terminal or aborted, else reconnect after 1s.
            if (signal.aborted) return;
            const s = cleanupJob?.status;
            if (s === 'COMPLETED' || s === 'FAILED' || s === 'CANCELLED') return;
            await new Promise(r => setTimeout(r, 1000));
          } catch (e) {
            if (signal.aborted) return;
            await new Promise(r => setTimeout(r, 3000));
          }
        }
      } finally {
        if (cleanupSseAbort?.signal === signal) cleanupSseAbort = null;
      }
    })();
  }

  function closeCleanupSse() {
    cleanupSseAbort?.abort();
    cleanupSseAbort = null;
  }

  async function cancelCleanup() {
    if (!cleanupJob) return;
    try {
      await apiFetch(`/api/v1/documents/cleanup/jobs/${cleanupJob.jobId}`, { method: 'DELETE' });
      toast.info('Cancellation requested.');
    } catch (e) {
      toast.error(`Cancel failed: ${e}`);
    }
  }

  function resetCleanupFilters() {
    cleanupFilters = { statuses: [], createdAfter: '', createdBefore: '', domain: '', contentType: '', uploadOrigin: '', metadataSource: '' };
    cleanupPreview = null;
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
        platformDefaultModel = data.default_model ?? null;
      }
      if (settingsRes.ok) {
        tenantSettings = await settingsRes.json();
        // Show the effective model in the select even if tenant has no explicit preference.
        // This prevents the dropdown from appearing blank. The save button remains disabled
        // when selection matches the stored llmModel (null check handled below).
        selectedModel = tenantSettings?.llmModel ?? tenantSettings?.effectiveModel ?? platformDefaultModel ?? null;
        thinkingMode = tenantSettings?.thinkingMode ?? false;
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

      // 2. Invalidate semantic cache + rag-service resolver cache
      await apiFetch(`/api/v1/admin/cache/clear/${tenantId}`, { method: 'POST' });
      await apiFetch(`/api/v1/tenants/${tenantId}/settings/invalidate-cache`, { method: 'DELETE' });

      selectedModel = pendingModel;
      tenantSettings = await res.json();
      toast.success('Model updated and cache cleared.');
    } catch (e) {
      toast.error(`Failed to update model: ${e}`);
    }
    modelSaving = false;
    pendingModel = null;
  }

  async function saveThinkingMode(enabled: boolean) {
    thinkingSaving = true;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/settings`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thinkingMode: enabled }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      tenantSettings = await res.json();
      thinkingMode = tenantSettings?.thinkingMode ?? false;
      // Bust both caches: resolver (in-process TTL) and semantic (Qdrant).
      // Semantic cache must be cleared because cached responses were generated
      // without/with thinking tokens and are now stale after the mode change.
      await Promise.all([
        apiFetch(`/api/v1/tenants/${tenantId}/settings/invalidate-cache`, { method: 'DELETE' }),
        apiFetch(`/api/v1/admin/cache/clear/${tenantId}`, { method: 'POST' }),
      ]);
      toast.success(enabled ? 'Thinking mode enabled — caches cleared.' : 'Thinking mode disabled — caches cleared.');
    } catch (e) {
      toast.error(`Failed to update thinking mode: ${e}`);
      thinkingMode = !enabled; // revert toggle
    }
    thinkingSaving = false;
  }

  async function loadCache() {
    cacheLoading = true;
    try {
      const res = await apiFetch('/api/v1/admin/cache/stats');
      if (res.ok) cacheStats = await res.json();
    } catch (e) {
      toast.error(`Failed to load cache stats: ${e}`);
    }
    cacheLoading = false;
  }

  async function clearSemanticCache() {
    semanticCacheConfirmOpen = false;
    clearingSemanticCache = true;
    try {
      const res = await apiFetch(`/api/v1/admin/cache/clear/${tenantId}`, { method: 'POST' });
      if (!res.ok) throw new Error(`${res.status}`);
      toast.success('Semantic cache cleared. Next queries will re-run through the LLM.');
      await loadCache();
    } catch (e) {
      toast.error(`Failed to clear semantic cache: ${e}`);
    }
    clearingSemanticCache = false;
  }

  async function clearResolverCache() {
    clearingResolverCache = true;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/settings/invalidate-cache`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`${res.status}`);
      toast.success('Model settings cache refreshed.');
    } catch (e) {
      toast.error(`Failed to refresh settings cache: ${e}`);
    }
    clearingResolverCache = false;
  }

  function switchTab(tab: typeof activeTab) {
    activeTab = tab;
    if (tab === 'usage') loadUsage();
    if (tab === 'documents') loadDocuments();
    if (tab === 'users') loadUsers();
    if (tab === 'model') loadModel();
    if (tab === 'cache') loadCache();
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
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Manage</h1>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">Tenant: <span class="font-mono font-medium">{tenantId}</span></p>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700">
      {#each [
        { id: 'usage', label: 'Usage' },
        { id: 'documents', label: 'Documents' },
        { id: 'users', label: 'Users' },
        ...(isTenantAdmin() ? [{ id: 'model', label: 'Model' }, { id: 'cache', label: 'Cache' }] : []),
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
      <div class="space-y-4">

        <!-- Bulk Cleanup Panel -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <button
            class="w-full flex items-center justify-between px-4 py-3 text-left"
            onclick={() => { cleanupFiltersOpen = !cleanupFiltersOpen; }}
          >
            <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">Bulk Cleanup</span>
            <svg class="w-4 h-4 text-gray-400 transition-transform {cleanupFiltersOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {#if cleanupFiltersOpen}
            <div class="border-t border-gray-100 dark:border-gray-700 px-4 pt-4 pb-4 space-y-4">

              <!-- Active job progress -->
              {#if cleanupJob && (cleanupJob.status === 'QUEUED' || cleanupJob.status === 'RUNNING')}
                <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700/40 rounded-lg p-4">
                  <div class="flex items-center justify-between mb-2">
                    <p class="text-sm font-medium text-blue-800 dark:text-blue-300">
                      Cleanup in progress — {cleanupJob.processed}/{cleanupJob.total} processed
                    </p>
                    <button
                      onclick={cancelCleanup}
                      class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                    >Cancel</button>
                  </div>
                  <div class="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-1.5">
                    <div
                      class="bg-blue-600 dark:bg-blue-400 h-1.5 rounded-full transition-all duration-300"
                      style="width: {cleanupJob.total > 0 ? Math.round((cleanupJob.processed / cleanupJob.total) * 100) : 0}%"
                    ></div>
                  </div>
                  <p class="text-xs text-blue-600 dark:text-blue-400 mt-1">
                    {cleanupJob.succeeded} deleted · {cleanupJob.failed} failed
                  </p>
                </div>

              {:else if cleanupJob && (cleanupJob.status === 'COMPLETED' || cleanupJob.status === 'FAILED' || cleanupJob.status === 'CANCELLED')}
                <div class="text-sm rounded-lg px-3 py-2 border {cleanupJob.status === 'COMPLETED' ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-700/40 text-green-800 dark:text-green-300' : 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-700/40 text-yellow-800 dark:text-yellow-300'}">
                  Last job: {cleanupJob.status} — {cleanupJob.succeeded} deleted, {cleanupJob.failed} failed
                  <button aria-label="Dismiss" onclick={() => cleanupJob = null} class="ml-2 opacity-60 hover:opacity-100">✕</button>
                </div>
              {/if}

              <!-- Filters grid -->
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">

                <div>
                  <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Status</label>
                  <div class="flex flex-wrap gap-1">
                    {#each ['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'] as s}
                      <label class="flex items-center gap-1 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={cleanupFilters.statuses.includes(s)}
                          onchange={() => {
                            cleanupFilters.statuses = cleanupFilters.statuses.includes(s)
                              ? cleanupFilters.statuses.filter(x => x !== s)
                              : [...cleanupFilters.statuses, s];
                            cleanupPreview = null;
                          }}
                          class="rounded text-blue-600"
                        />
                        <span class="text-xs text-gray-700 dark:text-gray-300">{s}</span>
                      </label>
                    {/each}
                  </div>
                </div>

                <div>
                  <label for="cf-origin" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Source</label>
                  <select
                    id="cf-origin"
                    bind:value={cleanupFilters.uploadOrigin}
                    onchange={() => cleanupPreview = null}
                    class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="">All sources</option>
                    <option value="MANUAL">Manual uploads only</option>
                    <option value="DATA_SOURCE">Data source / loader only</option>
                  </select>
                </div>

                <div>
                  <label for="cf-domain" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Domain (type)</label>
                  <select
                    id="cf-domain"
                    bind:value={cleanupFilters.domain}
                    onchange={() => cleanupPreview = null}
                    class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="">All domains</option>
                    {#each Object.entries(DOMAIN_LABELS) as [key, label]}
                      <option value={key}>{label}</option>
                    {/each}
                  </select>
                </div>

                <div>
                  <label for="cf-meta-source" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Metadata source tag</label>
                  <input
                    id="cf-meta-source"
                    type="text"
                    placeholder="e.g. sample_dataset"
                    bind:value={cleanupFilters.metadataSource}
                    oninput={() => cleanupPreview = null}
                    class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                <div>
                  <label for="cf-after" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Uploaded after</label>
                  <input
                    id="cf-after"
                    type="date"
                    bind:value={cleanupFilters.createdAfter}
                    oninput={() => cleanupPreview = null}
                    class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                <div>
                  <label for="cf-before" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Uploaded before</label>
                  <input
                    id="cf-before"
                    type="date"
                    bind:value={cleanupFilters.createdBefore}
                    oninput={() => cleanupPreview = null}
                    class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                <div class="sm:col-span-2">
                  <label for="cf-content-type" class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Content type prefix</label>
                  <input
                    id="cf-content-type"
                    type="text"
                    placeholder="e.g. application/pdf or image/"
                    bind:value={cleanupFilters.contentType}
                    oninput={() => cleanupPreview = null}
                    class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                  />
                </div>
              </div>

              <!-- Preview + action row -->
              <div class="flex items-center gap-3 pt-1">
                <button
                  onclick={previewCleanup}
                  disabled={cleanupPreviewing}
                  class="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
                >
                  {cleanupPreviewing ? 'Checking…' : 'Preview'}
                </button>

                {#if cleanupPreview !== null}
                  <span class="text-xs text-gray-500 dark:text-gray-400">
                    {cleanupPreview.matchCount === 0 ? 'No documents match.' : `${cleanupPreview.matchCount} document${cleanupPreview.matchCount === 1 ? '' : 's'} will be deleted.`}
                  </span>
                  {#if cleanupPreview.matchCount > 0}
                    <button
                      onclick={() => cleanupConfirmOpen = true}
                      disabled={cleanupStarting || (cleanupJob?.status === 'QUEUED' || cleanupJob?.status === 'RUNNING')}
                      class="px-3 py-1.5 text-xs font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                    >
                      {cleanupStarting ? 'Starting…' : `Delete ${cleanupPreview.matchCount}`}
                    </button>
                  {/if}
                {/if}

                <button
                  onclick={resetCleanupFilters}
                  class="ml-auto text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >Reset filters</button>
              </div>

            </div>
          {/if}
        </div>

        <!-- Document list -->
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
      </div>
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
              <p class="text-gray-500 dark:text-gray-500 text-xs mt-1">Users are managed via Zitadel. Ensure the service account PAT is configured.</p>
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
          {#if isPlatformControlled}
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
                  {#each availableModels as model}
                    <option value={model.name}>
                      {model.name}{model.name === platformDefaultModel ? ' (Platform Default)' : ''}
                    </option>
                  {/each}
                </select>
              </div>
              <button
                onclick={() => requestModelChange(selectedModel)}
                disabled={isPlatformControlled || modelSaving || selectedModel === (tenantSettings?.llmModel ?? tenantSettings?.effectiveModel ?? platformDefaultModel)}
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

          <!-- Thinking mode toggle -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div class="flex items-start justify-between gap-4">
              <div class="flex-1">
                <h3 class="text-base font-semibold text-gray-900 dark:text-white mb-1">Thinking Mode</h3>
                <p class="text-sm text-gray-500 dark:text-gray-400">
                  When enabled, the model reasons step-by-step before answering (shown in a collapsible panel).
                  Best for complex multi-step analysis. For straightforward factual retrieval, keeping this
                  <span class="font-medium">off</span> reduces latency and often gives better results.
                </p>
                {#if !activeModelSupportsThinking}
                  <p class="text-xs text-amber-600 dark:text-amber-400 mt-2">
                    The active model does not support thinking mode.
                  </p>
                {/if}
              </div>
              <button
                role="switch"
                aria-checked={thinkingMode}
                disabled={!activeModelSupportsThinking || thinkingSaving}
                onclick={() => saveThinkingMode(!thinkingMode)}
                class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent
                  transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2
                  disabled:opacity-40 disabled:cursor-not-allowed mt-0.5
                  {thinkingMode && activeModelSupportsThinking ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'}"
              >
                <span
                  class="pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow ring-0
                    transition duration-200 ease-in-out
                    {thinkingMode && activeModelSupportsThinking ? 'translate-x-5' : 'translate-x-0'}"
                ></span>
              </button>
            </div>
          </div>

        </div>
      {/if}
    {/if}

    <!-- Cache Tab -->
    {#if activeTab === 'cache'}
      {#if cacheLoading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else}
        <div class="space-y-4">

          <!-- Semantic Query Cache -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div class="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Semantic Query Cache</h3>
                <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                  Stores AI-generated responses for semantically similar queries to reduce latency.
                  Clear this when you change the model, toggle thinking mode, or want fresh responses.
                </p>
              </div>
              <button
                onclick={() => semanticCacheConfirmOpen = true}
                disabled={clearingSemanticCache}
                class="shrink-0 px-3 py-1.5 text-sm font-medium rounded-lg border
                  border-red-300 dark:border-red-700 text-red-600 dark:text-red-400
                  hover:bg-red-50 dark:hover:bg-red-900/20
                  disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {clearingSemanticCache ? 'Clearing…' : 'Clear Cache'}
              </button>
            </div>
            {#if cacheStats}
              <div class="grid grid-cols-2 gap-4">
                <div class="bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3">
                  <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">Cached responses</p>
                  <p class="text-xl font-semibold text-gray-900 dark:text-white">{cacheStats.totalEntries.toLocaleString()}</p>
                </div>
                <div class="bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3">
                  <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">Cache hit rate</p>
                  <p class="text-xl font-semibold text-gray-900 dark:text-white">{(cacheStats.hitRate * 100).toFixed(1)}%</p>
                </div>
              </div>
            {:else}
              <p class="text-sm text-gray-400">Stats unavailable.</p>
            {/if}
          </div>

          <!-- Model Settings Cache -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Model Settings Cache</h3>
                <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                  In-process cache of your tenant's model and thinking mode preferences.
                  Auto-expires every 60 seconds. Force-refresh if a settings change isn't
                  taking effect immediately.
                </p>
                <p class="text-xs text-gray-400 dark:text-gray-500 mt-2">
                  This cache is automatically cleared when you save model or thinking mode settings.
                </p>
              </div>
              <button
                onclick={clearResolverCache}
                disabled={clearingResolverCache}
                class="shrink-0 px-3 py-1.5 text-sm font-medium rounded-lg border
                  border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400
                  hover:bg-gray-50 dark:hover:bg-gray-700
                  disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {clearingResolverCache ? 'Refreshing…' : 'Force Refresh'}
              </button>
            </div>
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
  message={`This will update the active model to "${pendingModel}" and clear the semantic cache for your tenant. Cached responses will be regenerated on the next query. Proceed?`}
  confirmLabel="Change & Clear Cache"
  dangerous={false}
  onconfirm={confirmModelChange}
  oncancel={() => { modelConfirmOpen = false; pendingModel = null; }}
/>

<ConfirmDialog
  open={semanticCacheConfirmOpen}
  title="Clear semantic cache?"
  message="This will delete all cached query responses for your tenant. The next queries will be slower as they re-run through the LLM. This cannot be undone."
  confirmLabel="Clear Cache"
  dangerous={true}
  onconfirm={clearSemanticCache}
  oncancel={() => { semanticCacheConfirmOpen = false; }}
/>

<ConfirmDialog
  open={cleanupConfirmOpen}
  title="Delete documents?"
  message={`This will permanently delete ${cleanupPreview?.matchCount ?? 0} document${(cleanupPreview?.matchCount ?? 0) === 1 ? '' : 's'} matching your filters — including all chunks and vectors. This cannot be undone.`}
  confirmLabel={`Delete ${cleanupPreview?.matchCount ?? 0}`}
  dangerous={true}
  onconfirm={startCleanup}
  oncancel={() => { cleanupConfirmOpen = false; }}
/>
