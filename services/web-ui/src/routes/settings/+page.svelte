<script lang="ts">
  import { onMount } from 'svelte';
  import { getTenantId, getAuthState, isTenantAdmin, authStore } from '$lib/auth';
  import { apiFetch } from '$lib/api';

  // Reactive auth state — mirrors what the layout does so role-gated sections
  // re-evaluate if the store updates after initial render (Svelte 5 runes mode
  // does not track get(store) calls as reactive dependencies).
  let _authState = $state(getAuthState());
  authStore.subscribe(s => { _authState = s; });
  const isAdmin = $derived(
    _authState.user?.role === 'tenant_admin' || _authState.user?.role === 'platform_admin'
  );
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

  let activeTab: 'usage' | 'documents' | 'users' | 'model' | 'cache' | 'preferences' | 'analytics' = $state('usage');
  const _isPlatformAdmin = $derived(_authState.user?.role === 'platform_admin');
  let loading = $state(true);

  // Usage
  let usage: any = $state(null);

  // Documents
  interface Doc {
    id: string;
    filename: string;
    contentType: string | null;
    fileSize: number;
    chunkCount: number;
    status: string;
    metadata: Record<string, string>;
    createdAt: string;
    updatedAt: string;
  }
  interface ChunkPreview {
    id: string; chunkIndex: number; content: string; tokenCount: number;
  }
  interface DocStats {
    totalDocuments: number; totalChunks: number; totalBytes: number;
    byStatus: Record<string, number>; byDomain: Record<string, number>;
    bySource: Record<string, number>; lastUploadedAt: string | null;
  }
  let documents: Doc[] = $state([]);
  let docsLoading = $state(false);
  let docStats: DocStats | null = $state(null);
  let docStatsLoading = $state(false);
  // Pagination
  let docCurrentPage = $state(0);
  let docPageSize = $state(20);
  let docTotalPages = $state(0);
  let docTotalElements = $state(0);
  // Expandable rows
  let expandedDocId: string | null = $state(null);
  let chunkCache: Record<string, ChunkPreview[]> = $state({});
  let loadingPreviewFor: string | null = $state(null);
  // Platform admin tenant selector for docs tab
  let allTenants: { tenantId: string; name: string }[] = $state([]);
  let selectedDocTenant = $state('');  // empty = current JWT tenant
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
  interface TenantSettings { llmModel: string | null; effectiveModel: string | null }
  let modelLoading = $state(false);
  let availableModels: ModelInfo[] = $state([]);
  let platformDefaultModel: string | null = $state(null);
  let tenantSettings: TenantSettings | null = $state(null);
  let selectedModel: string | null = $state(null);   // null = "Platform Default"
  let modelSaving = $state(false);
  let modelConfirmOpen = $state(false);
  let pendingModel: string | null = $state(null);

  // Tenant model profiles
  interface ModelProfile {
    id: string; scope: string; tenantId: string | null; modelPattern: string;
    displayName: string | null;
    // Standard mode
    temperature: number | null; topP: number | null; maxTokens: number | null;
    frequencyPenalty: number | null; presencePenalty: number | null;
    repetitionPenalty: number | null; topK: number | null; minP: number | null;
    // Thinking mode
    thinkingTemperature: number | null; thinkingTopP: number | null; thinkingMaxTokens: number | null;
    thinkingFrequencyPenalty: number | null; thinkingPresencePenalty: number | null;
    thinkingRepetitionPenalty: number | null; thinkingTopK: number | null;
    thinkingMinP: number | null; thinkingBudget: number | null;
    notes: string | null;
  }
  const EMPTY_PROFILE_FORM = () => ({
    modelPattern: '', displayName: '',
    temperature: '', topP: '', maxTokens: '', frequencyPenalty: '',
    presencePenalty: '', repetitionPenalty: '', topK: '', minP: '',
    thinkingTemperature: '', thinkingTopP: '', thinkingMaxTokens: '',
    thinkingFrequencyPenalty: '', thinkingPresencePenalty: '',
    thinkingRepetitionPenalty: '', thinkingTopK: '', thinkingMinP: '',
    thinkingBudget: '',
    notes: '',
  });
  let tenantProfiles: ModelProfile[] = $state([]);
  let profileModalOpen = $state(false);
  let profileModalMode: 'create' | 'edit' = $state('create');
  let editingProfileId: string | null = $state(null);
  let profileForm = $state(EMPTY_PROFILE_FORM());
  let profileSaving = $state(false);
  let profileDeleteConfirmId: string | null = $state(null);

  // The tenant's * wildcard profile — the "tenant defaults" that are always edited in-place.
  let wildcardProfile = $derived(tenantProfiles.find(p => p.modelPattern === '*') ?? null);
  // Non-wildcard profiles shown in the "Model-specific Overrides" table.
  let specificProfiles = $derived(tenantProfiles.filter(p => p.modelPattern !== '*'));

  // Inline edit state for the * wildcard (tenant defaults)
  let defaultsEditMode = $state(false);
  let defaultsForm = $state({
    temperature: '', topP: '', maxTokens: '', frequencyPenalty: '',
    presencePenalty: '', repetitionPenalty: '', topK: '', minP: '',
    thinkingTemperature: '', thinkingTopP: '', thinkingMaxTokens: '',
    thinkingFrequencyPenalty: '', thinkingPresencePenalty: '',
    thinkingRepetitionPenalty: '', thinkingTopK: '', thinkingMinP: '',
    thinkingBudget: '',
    notes: '',
  });
  let defaultsSaving = $state(false);
  let seedingDefaults = $state(false);

  function syncDefaultsForm(p: ModelProfile) {
    const s = (v: number | null) => v != null ? String(v) : '';
    defaultsForm = {
      temperature:              s(p.temperature),
      topP:                     s(p.topP),
      maxTokens:                s(p.maxTokens),
      frequencyPenalty:         s(p.frequencyPenalty),
      presencePenalty:          s(p.presencePenalty),
      repetitionPenalty:        s(p.repetitionPenalty),
      topK:                     s(p.topK),
      minP:                     s(p.minP),
      thinkingTemperature:      s(p.thinkingTemperature),
      thinkingTopP:             s(p.thinkingTopP),
      thinkingMaxTokens:        s(p.thinkingMaxTokens),
      thinkingFrequencyPenalty: s(p.thinkingFrequencyPenalty),
      thinkingPresencePenalty:  s(p.thinkingPresencePenalty),
      thinkingRepetitionPenalty:s(p.thinkingRepetitionPenalty),
      thinkingTopK:             s(p.thinkingTopK),
      thinkingMinP:             s(p.thinkingMinP),
      thinkingBudget:           s(p.thinkingBudget),
      notes: p.notes ?? '',
    };
  }

  async function saveDefaults() {
    if (!wildcardProfile) return;
    defaultsSaving = true;
    try {
      const num = (v: string | number) => typeof v === 'number' ? (isNaN(v) ? null : v) : (v.trim() === '' ? null : Number(v));
      const int = (v: string | number) => typeof v === 'number' ? (isNaN(v) ? null : Math.round(v)) : (v.trim() === '' ? null : parseInt(v));
      // Validate: thinking_budget must be less than thinking_max_tokens when both set
      const budget = int(defaultsForm.thinkingBudget);
      const maxTok = int(defaultsForm.thinkingMaxTokens);
      if (budget != null && maxTok != null && budget >= maxTok) {
        toast.error('Thinking budget must be less than thinking max tokens.');
        defaultsSaving = false;
        return;
      }
      const body = {
        modelPattern: '*',
        displayName: wildcardProfile.displayName,
        temperature:               num(defaultsForm.temperature),
        topP:                      num(defaultsForm.topP),
        maxTokens:                 int(defaultsForm.maxTokens),
        frequencyPenalty:          num(defaultsForm.frequencyPenalty),
        presencePenalty:           num(defaultsForm.presencePenalty),
        repetitionPenalty:         num(defaultsForm.repetitionPenalty),
        topK:                      int(defaultsForm.topK),
        minP:                      num(defaultsForm.minP),
        thinkingTemperature:       num(defaultsForm.thinkingTemperature),
        thinkingTopP:              num(defaultsForm.thinkingTopP),
        thinkingMaxTokens:         int(defaultsForm.thinkingMaxTokens),
        thinkingFrequencyPenalty:  num(defaultsForm.thinkingFrequencyPenalty),
        thinkingPresencePenalty:   num(defaultsForm.thinkingPresencePenalty),
        thinkingRepetitionPenalty: num(defaultsForm.thinkingRepetitionPenalty),
        thinkingTopK:              int(defaultsForm.thinkingTopK),
        thinkingMinP:              num(defaultsForm.thinkingMinP),
        thinkingBudget:            int(defaultsForm.thinkingBudget),
        notes: defaultsForm.notes.trim() || null,
      };
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles/${wildcardProfile.id}`, {
        method: 'PUT', headers: jsonHeaders(), body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles-cache`, { method: 'DELETE' });
      toast.success('Tenant defaults saved.');
      defaultsEditMode = false;
      const prRes = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles`);
      if (prRes.ok) tenantProfiles = await prRes.json();
    } catch (e) { toast.error(`Failed: ${e}`); }
    defaultsSaving = false;
  }

  async function initializeDefaults() {
    seedingDefaults = true;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles/seed`, { method: 'POST' });
      if (!res.ok) throw new Error(`${res.status}`);
      toast.success('Defaults initialized.');
      const prRes = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles`);
      if (prRes.ok) {
        tenantProfiles = await prRes.json();
        const wp = tenantProfiles.find(p => p.modelPattern === '*');
        if (wp) syncDefaultsForm(wp);
      }
    } catch (e) { toast.error(`Failed: ${e}`); }
    seedingDefaults = false;
  }

  // Personal Preferences
  interface UserPreferences { thinkingMode: boolean }
  let preferencesLoading = $state(false);
  let userPreferences: UserPreferences | null = $state(null);
  let thinkingMode = $state(false);
  let thinkingSaving = $state(false);

  // Analytics
  interface QuerySummary { totalQueries: number; avgLatencyMs: number; cacheHitRate: number; p95LatencyMs?: number }
  interface FeedbackSummary { totalFeedback: number; likes: number; dislikes: number; likeRate: number }
  interface TimeseriesPoint { ts: string; count: number; avg_latency_ms: number; p95_latency_ms?: number; cache_hit_rate?: number }
  interface FeedbackPoint { ts: string; likes: number; dislikes: number }
  interface ModelStat { model: string; count: number; avg_latency_ms: number }
  let analyticsLoading = $state(false);
  let querySummary: QuerySummary | null = $state(null);
  let feedbackSummary: FeedbackSummary | null = $state(null);
  let queryTimeseries: TimeseriesPoint[] = $state([]);
  let feedbackTimeseries: FeedbackPoint[] = $state([]);
  let modelStats: ModelStat[] = $state([]);
  let analyticsSelectedTenant = $state('');  // '' = current JWT tenant (platform admin only)

  async function loadAnalytics() {
    analyticsLoading = true;
    const tp = analyticsSelectedTenant && analyticsSelectedTenant !== tenantId
      ? `?tenant_id=${encodeURIComponent(analyticsSelectedTenant)}` : '';
    try {
      const [qs, fs, qt, ft, ms] = await Promise.all([
        fetchJson(`/api/v1/analytics/queries/summary${tp}`),
        fetchJson(`/api/v1/analytics/feedback/summary${tp}`),
        fetchJson(`/api/v1/analytics/queries/timeseries${tp ? tp + '&days=30' : '?days=30'}`),
        fetchJson(`/api/v1/analytics/feedback/timeseries${tp ? tp + '&days=30' : '?days=30'}`),
        fetchJson(`/api/v1/analytics/queries/by-model${tp ? tp + '&days=30' : '?days=30'}`),
      ]);
      querySummary = qs;
      feedbackSummary = fs;
      queryTimeseries = qt ?? [];
      feedbackTimeseries = ft ?? [];
      modelStats = ms ?? [];
    } catch (e) { toast.error(`Analytics load failed: ${e}`); }
    analyticsLoading = false;
  }

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

  function effectiveDocTenant(): string {
    return selectedDocTenant || tenantId;
  }

  function docTenantParam(): string {
    const t = selectedDocTenant;
    return t && t !== tenantId ? `&tenant_id=${encodeURIComponent(t)}` : '';
  }

  async function loadDocuments(page: number = 0) {
    docsLoading = true;
    const params = `page=${page}&size=${docPageSize}&sort=createdAt,desc${docTenantParam()}`;
    const data = await fetchJson(`/api/v1/documents?${params}`);
    if (data?.content) {
      documents = data.content;
      docTotalPages = data.totalPages ?? 1;
      docTotalElements = data.totalElements ?? data.content.length;
      docCurrentPage = data.number ?? page;
    } else {
      documents = Array.isArray(data) ? data : [];
      docTotalPages = 1;
      docTotalElements = documents.length;
      docCurrentPage = 0;
    }
    docsLoading = false;
  }

  async function loadDocStats() {
    docStatsLoading = true;
    docStats = await fetchJson(`/api/v1/documents/stats${docTenantParam() ? '?' + docTenantParam().slice(1) : ''}`);
    docStatsLoading = false;
  }

  async function loadAllTenants() {
    if (!_isPlatformAdmin) return;
    const data = await fetchJson('/api/v1/tenants');
    allTenants = data ?? [];
    if (!selectedDocTenant && allTenants.length > 0) {
      selectedDocTenant = tenantId;
    }
  }

  function goToDocPage(page: number) {
    if (page >= 0 && page < docTotalPages) loadDocuments(page);
  }

  async function refreshDocs() {
    await Promise.all([loadDocuments(0), loadDocStats()]);
  }

  async function toggleDocPreview(doc: Doc) {
    if (expandedDocId === doc.id) { expandedDocId = null; return; }
    expandedDocId = doc.id;
    if (chunkCache[doc.id]) return;
    loadingPreviewFor = doc.id;
    try {
      const tp = docTenantParam();
      const url = `/api/v1/documents/${doc.id}?include_chunks=true${tp}`;
      const res = await apiFetch(url);
      if (res.ok) {
        const detail = await res.json();
        chunkCache = { ...chunkCache, [doc.id]: detail.chunks ?? [] };
      }
    } catch {}
    loadingPreviewFor = null;
  }

  function formatFileSize(bytes: number): string {
    if (!bytes) return '0 B';
    const k = 1024, sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  }

  function getStatusColor(status: string): string {
    const m: Record<string, string> = {
      COMPLETED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
      PROCESSING: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
      PENDING: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-400',
      FAILED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    };
    return m[status] ?? m.PENDING;
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
      const tp = docTenantParam();
      const url = `/api/v1/documents/${confirmDocId}${tp ? '?' + tp.slice(1) : ''}`;
      const res = await apiFetch(url, { method: 'DELETE' });
      if (res.ok) {
        toast.success(`Deleted "${confirmDocName}"`);
        if (expandedDocId === confirmDocId) expandedDocId = null;
        delete chunkCache[confirmDocId];
        await refreshDocs();
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
      const [modelsRes, settingsRes, profilesRes] = await Promise.all([
        apiFetch('/api/v1/models'),
        apiFetch(`/api/v1/tenants/${tenantId}/settings`),
        isAdmin ? apiFetch(`/api/v1/tenants/${tenantId}/model-profiles`) : Promise.resolve(null),
      ]);
      if (modelsRes.ok) {
        const data = await modelsRes.json();
        availableModels = data.models ?? [];
        platformDefaultModel = data.default_model ?? null;
      }
      if (settingsRes.ok) {
        tenantSettings = await settingsRes.json();
        selectedModel = tenantSettings?.llmModel ?? tenantSettings?.effectiveModel ?? platformDefaultModel ?? null;
      }
      if (profilesRes?.ok) {
        tenantProfiles = await profilesRes.json();
        const wp = tenantProfiles.find(p => p.modelPattern === '*');
        if (wp) syncDefaultsForm(wp);
      }
    } catch (e) {
      toast.error(`Failed to load model settings: ${e}`);
    }
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
      modelPattern:              p.modelPattern,
      displayName:               p.displayName ?? '',
      temperature:               s(p.temperature),
      topP:                      s(p.topP),
      maxTokens:                 s(p.maxTokens),
      frequencyPenalty:          s(p.frequencyPenalty),
      presencePenalty:           s(p.presencePenalty),
      repetitionPenalty:         s(p.repetitionPenalty),
      topK:                      s(p.topK),
      minP:                      s(p.minP),
      thinkingTemperature:       s(p.thinkingTemperature),
      thinkingTopP:              s(p.thinkingTopP),
      thinkingMaxTokens:         s(p.thinkingMaxTokens),
      thinkingFrequencyPenalty:  s(p.thinkingFrequencyPenalty),
      thinkingPresencePenalty:   s(p.thinkingPresencePenalty),
      thinkingRepetitionPenalty: s(p.thinkingRepetitionPenalty),
      thinkingTopK:              s(p.thinkingTopK),
      thinkingMinP:              s(p.thinkingMinP),
      thinkingBudget:            s(p.thinkingBudget),
      notes:                     p.notes ?? '',
    };
    profileModalOpen = true;
  }

  function profileFormBody() {
    const num = (v: string | number) => typeof v === 'number' ? (isNaN(v) ? null : v) : (v.trim() === '' ? null : Number(v));
    const int = (v: string | number) => typeof v === 'number' ? (isNaN(v) ? null : Math.round(v)) : (v.trim() === '' ? null : parseInt(v));
    return {
      modelPattern:              profileForm.modelPattern.trim(),
      displayName:               profileForm.displayName.trim() || null,
      temperature:               num(profileForm.temperature),
      topP:                      num(profileForm.topP),
      maxTokens:                 int(profileForm.maxTokens),
      frequencyPenalty:          num(profileForm.frequencyPenalty),
      presencePenalty:           num(profileForm.presencePenalty),
      repetitionPenalty:         num(profileForm.repetitionPenalty),
      topK:                      int(profileForm.topK),
      minP:                      num(profileForm.minP),
      thinkingTemperature:       num(profileForm.thinkingTemperature),
      thinkingTopP:              num(profileForm.thinkingTopP),
      thinkingMaxTokens:         int(profileForm.thinkingMaxTokens),
      thinkingFrequencyPenalty:  num(profileForm.thinkingFrequencyPenalty),
      thinkingPresencePenalty:   num(profileForm.thinkingPresencePenalty),
      thinkingRepetitionPenalty: num(profileForm.thinkingRepetitionPenalty),
      thinkingTopK:              int(profileForm.thinkingTopK),
      thinkingMinP:              num(profileForm.thinkingMinP),
      thinkingBudget:            int(profileForm.thinkingBudget),
      notes:                     profileForm.notes.trim() || null,
    };
  }

  async function saveProfile() {
    if (!profileForm.modelPattern.trim()) { toast.error('Model pattern is required'); return; }
    profileSaving = true;
    try {
      const url = profileModalMode === 'create'
        ? `/api/v1/tenants/${tenantId}/model-profiles`
        : `/api/v1/tenants/${tenantId}/model-profiles/${editingProfileId}`;
      const method = profileModalMode === 'create' ? 'POST' : 'PUT';
      const res = await apiFetch(url, { method, headers: jsonHeaders(), body: JSON.stringify(profileFormBody()) });
      if (!res.ok) throw new Error(`${res.status}`);
      await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles-cache`, { method: 'DELETE' });
      toast.success(profileModalMode === 'create' ? 'Profile created' : 'Profile updated');
      profileModalOpen = false;
      const prRes = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles`);
      if (prRes.ok) tenantProfiles = await prRes.json();
    } catch (e) { toast.error(`Failed: ${e}`); }
    profileSaving = false;
  }

  async function deleteProfile(id: string) {
    profileDeleteConfirmId = null;
    try {
      const res = await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`${res.status}`);
      await apiFetch(`/api/v1/tenants/${tenantId}/model-profiles-cache`, { method: 'DELETE' });
      toast.success('Profile deleted');
      tenantProfiles = tenantProfiles.filter(p => p.id !== id);
    } catch (e) { toast.error(`Failed: ${e}`); }
  }

  async function loadPreferences() {
    preferencesLoading = true;
    try {
      const res = await apiFetch('/api/v1/users/me/preferences');
      if (res.ok) {
        userPreferences = await res.json();
        thinkingMode = userPreferences?.thinkingMode ?? false;
      }
      // Always refresh models + tenant settings on preferences load so
      // activeModelSupportsThinking reflects the current LLM engine state.
      const [modelsRes, settingsRes] = await Promise.all([
        apiFetch('/api/v1/models'),
        apiFetch(`/api/v1/tenants/${tenantId}/settings`),
      ]);
      if (modelsRes.ok) {
        const data = await modelsRes.json();
        availableModels = data.models ?? [];
        platformDefaultModel = data.default_model ?? null;
      }
      if (settingsRes.ok) tenantSettings = await settingsRes.json();
    } catch (e) {
      toast.error(`Failed to load preferences: ${e}`);
    }
    preferencesLoading = false;
  }

  async function saveThinkingMode(enabled: boolean) {
    thinkingSaving = true;
    try {
      const res = await apiFetch('/api/v1/users/me/preferences', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thinkingMode: enabled }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      userPreferences = await res.json();
      thinkingMode = userPreferences?.thinkingMode ?? false;
      // Bust the user-scoped resolver cache so the next query picks up the new preference.
      await apiFetch('/api/v1/users/me/preferences/invalidate-cache', { method: 'DELETE' });
      // Also clear the semantic cache — cached answers are keyed by embedding only (not
      // by thinking flag), so stale non-thinking entries must be evicted on toggle.
      await apiFetch(`/api/v1/admin/cache/clear/${tenantId}`, { method: 'POST' });
      toast.success(enabled ? 'Thinking mode enabled.' : 'Thinking mode disabled.');
    } catch (e) {
      toast.error(`Failed to update thinking mode: ${e}`);
      thinkingMode = !enabled;
    }
    thinkingSaving = false;
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
    if (tab === 'documents') { loadAllTenants(); refreshDocs(); }
    if (tab === 'users') loadUsers();
    if (tab === 'model') loadModel();
    if (tab === 'cache') loadCache();
    if (tab === 'preferences') loadPreferences();
    if (tab === 'analytics') loadAnalytics();
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
        ...(isAdmin ? [{ id: 'model', label: 'Model' }, { id: 'cache', label: 'Cache' }, { id: 'analytics', label: 'Analytics' }] : []),
        { id: 'preferences', label: 'Preferences' },
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

        <!-- Platform admin: tenant selector -->
        {#if _isPlatformAdmin && allTenants.length > 0}
          <div class="flex items-center gap-3">
            <label class="text-sm text-gray-600 dark:text-gray-400 font-medium">Viewing tenant:</label>
            <select
              bind:value={selectedDocTenant}
              onchange={() => { expandedDocId = null; chunkCache = {}; refreshDocs(); }}
              class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-1 focus:ring-blue-500"
            >
              {#each allTenants as t}
                <option value={t.tenantId}>{t.name} ({t.tenantId})</option>
              {/each}
            </select>
          </div>
        {/if}

        <!-- Document Status Panel -->
        {#if docStats || docStatsLoading}
          <div class="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-xl border border-blue-200 dark:border-blue-800 p-5">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-sm font-semibold text-gray-800 dark:text-white">Document Overview</h3>
              {#if docStatsLoading}
                <div class="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
              {:else if docStats?.lastUploadedAt}
                <span class="text-xs text-gray-400">Last upload: {new Date(docStats.lastUploadedAt).toLocaleString()}</span>
              {/if}
            </div>
            {#if docStats}
              <!-- Top stats row -->
              <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                {#each [
                  { label: 'Total', value: docStats.totalDocuments, color: 'text-blue-600 dark:text-blue-400' },
                  { label: 'Indexed', value: docStats.byStatus['COMPLETED'] ?? 0, color: 'text-green-600 dark:text-green-400' },
                  { label: 'In-flight', value: (docStats.byStatus['PENDING'] ?? 0) + (docStats.byStatus['PROCESSING'] ?? 0), color: 'text-yellow-600 dark:text-yellow-400' },
                  { label: 'Failed', value: docStats.byStatus['FAILED'] ?? 0, color: 'text-red-600 dark:text-red-400' },
                  { label: 'Chunks', value: docStats.totalChunks, color: 'text-indigo-600 dark:text-indigo-400' },
                ] as s}
                  <div class="bg-white dark:bg-gray-800 rounded-lg p-3 text-center shadow-sm">
                    <div class="text-2xl font-bold {s.color}">{s.value.toLocaleString()}</div>
                    <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{s.label}</div>
                  </div>
                {/each}
              </div>
              <!-- Storage + domain/source chips -->
              <div class="flex flex-wrap gap-3 items-center">
                <span class="text-xs text-gray-500 dark:text-gray-400">
                  Storage: <span class="font-medium text-gray-700 dark:text-gray-300">{formatFileSize(docStats.totalBytes)}</span>
                </span>
                <span class="text-gray-300 dark:text-gray-600">|</span>
                <span class="text-xs text-gray-500 dark:text-gray-400">By domain:</span>
                {#each Object.entries(docStats.byDomain).filter(([,v]) => v > 0) as [domain, count]}
                  <span class="px-2 py-0.5 rounded-full text-xs font-medium {getDomainColor(domain)}">
                    {DOMAIN_LABELS[domain] ?? domain} <span class="opacity-70">{count}</span>
                  </span>
                {/each}
                <span class="text-gray-300 dark:text-gray-600">|</span>
                <span class="text-xs text-gray-500 dark:text-gray-400">By source:</span>
                {#if docStats.bySource['sample'] > 0}
                  <span class="px-2 py-0.5 rounded-full text-xs font-medium bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400">
                    Sample {docStats.bySource['sample']}
                  </span>
                {/if}
                {#if docStats.bySource['manual'] > 0}
                  <span class="px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400">
                    Manual {docStats.bySource['manual']}
                  </span>
                {/if}
              </div>
            {/if}
          </div>
        {/if}

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

        <!-- Document list with expandable rows + pagination -->
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <!-- Header row -->
          <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
            <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Documents
              {#if docTotalElements > 0}<span class="text-gray-400 font-normal ml-1">({docTotalElements})</span>{/if}
            </span>
            <div class="flex items-center gap-2">
              <label class="text-xs text-gray-500 dark:text-gray-400">Per page:</label>
              <select
                bind:value={docPageSize}
                onchange={() => loadDocuments(0)}
                class="text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1"
              >
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
              <button onclick={refreshDocs} disabled={docsLoading} class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 disabled:opacity-40">
                Refresh
              </button>
            </div>
          </div>

          {#if docsLoading}
            <div class="text-center py-12 text-gray-400">Loading...</div>
          {:else if documents.length === 0}
            <div class="text-center py-12 text-gray-400 text-sm">No documents found.</div>
          {:else}
            <table class="w-full text-sm">
              <thead class="bg-gray-50 dark:bg-gray-700/50 border-b border-gray-200 dark:border-gray-700">
                <tr>
                  <th class="w-8 px-3 py-3"></th>
                  <th class="text-left px-3 py-3 font-medium text-gray-700 dark:text-gray-300">File</th>
                  <th class="text-left px-3 py-3 font-medium text-gray-700 dark:text-gray-300">Domain</th>
                  <th class="text-left px-3 py-3 font-medium text-gray-700 dark:text-gray-300">Source</th>
                  <th class="text-left px-3 py-3 font-medium text-gray-700 dark:text-gray-300">Status</th>
                  <th class="text-right px-3 py-3 font-medium text-gray-700 dark:text-gray-300">Size</th>
                  <th class="text-right px-3 py-3 font-medium text-gray-700 dark:text-gray-300">Chunks</th>
                  <th class="text-left px-3 py-3 font-medium text-gray-700 dark:text-gray-300">Uploaded</th>
                  <th class="px-3 py-3"></th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {#each documents as doc}
                  <!-- Main row -->
                  <tr
                    class="hover:bg-gray-50 dark:hover:bg-gray-700/30 cursor-pointer transition-colors"
                    onclick={() => toggleDocPreview(doc)}
                  >
                    <td class="px-3 py-3 text-gray-400 dark:text-gray-500 select-none">
                      <svg class="w-4 h-4 transition-transform {expandedDocId === doc.id ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                      </svg>
                    </td>
                    <td class="px-3 py-3">
                      <span class="font-medium text-gray-900 dark:text-white truncate block max-w-[180px]" title={doc.filename}>{doc.filename}</span>
                      {#if doc.contentType}
                        <span class="text-xs text-gray-400 font-mono">{doc.contentType}</span>
                      {/if}
                    </td>
                    <td class="px-3 py-3">
                      {#if doc.metadata?.domain}
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {getDomainColor(doc.metadata.domain)}">
                          {DOMAIN_LABELS[doc.metadata.domain] ?? doc.metadata.domain}
                        </span>
                      {:else}
                        <span class="text-gray-400 text-xs">—</span>
                      {/if}
                    </td>
                    <td class="px-3 py-3">
                      {#if doc.metadata?.source === 'sample_dataset'}
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400">Sample</span>
                        {#if doc.metadata?.source_dataset}
                          <span class="block text-xs text-gray-400 mt-0.5">{doc.metadata.source_dataset}</span>
                        {/if}
                      {:else}
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400">Manual</span>
                      {/if}
                    </td>
                    <td class="px-3 py-3">
                      <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {getStatusColor(doc.status)}">{doc.status}</span>
                    </td>
                    <td class="px-3 py-3 text-right text-xs text-gray-500 dark:text-gray-400">{formatFileSize(doc.fileSize)}</td>
                    <td class="px-3 py-3 text-right text-xs text-gray-500 dark:text-gray-400">{doc.chunkCount}</td>
                    <td class="px-3 py-3 text-xs text-gray-500 dark:text-gray-400">{formatDate(doc.createdAt)}</td>
                    <td class="px-3 py-3 text-right">
                      <button
                        onclick={(e) => { e.stopPropagation(); confirmDeleteDoc(doc); }}
                        disabled={deleting}
                        class="px-2.5 py-1 text-xs rounded-lg text-red-600 dark:text-red-400
                          hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50 transition-colors"
                      >Delete</button>
                    </td>
                  </tr>
                  <!-- Expanded chunk preview row -->
                  {#if expandedDocId === doc.id}
                    <tr class="bg-gray-50 dark:bg-gray-900/40 border-b border-gray-100 dark:border-gray-700/50">
                      <td colspan="9" class="px-8 py-4">
                        {#if loadingPreviewFor === doc.id}
                          <div class="flex items-center gap-2 text-gray-400 text-sm">
                            <div class="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin"></div>
                            Loading chunks…
                          </div>
                        {:else if chunkCache[doc.id]?.length}
                          <div class="space-y-3 max-h-64 overflow-y-auto pr-2">
                            {#each chunkCache[doc.id] as chunk, i}
                              <div class="text-sm">
                                <span class="text-xs text-gray-400 dark:text-gray-500 font-mono">Chunk {chunk.chunkIndex + 1} · {chunk.tokenCount} tokens</span>
                                <p class="mt-1 text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-wrap">{chunk.content}</p>
                              </div>
                              {#if i < chunkCache[doc.id].length - 1}
                                <hr class="border-gray-200 dark:border-gray-700" />
                              {/if}
                            {/each}
                          </div>
                        {:else if doc.metadata?.content_preview}
                          <p class="text-xs text-gray-400 mb-1">Content preview</p>
                          <p class="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{doc.metadata.content_preview}</p>
                        {:else}
                          <p class="text-sm text-gray-400">No content preview available.</p>
                        {/if}
                      </td>
                    </tr>
                  {/if}
                {/each}
              </tbody>
            </table>

            <!-- Pagination -->
            {#if docTotalPages > 1}
              <div class="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-700">
                <p class="text-xs text-gray-500 dark:text-gray-400">
                  {docCurrentPage * docPageSize + 1}–{Math.min((docCurrentPage + 1) * docPageSize, docTotalElements)} of {docTotalElements}
                </p>
                <div class="flex items-center gap-1">
                  <button onclick={() => goToDocPage(0)} disabled={docCurrentPage === 0}
                    class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700">First</button>
                  <button onclick={() => goToDocPage(docCurrentPage - 1)} disabled={docCurrentPage === 0}
                    class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700">Prev</button>
                  {#each Array.from({length: Math.min(5, docTotalPages)}, (_, i) => {
                    const start = Math.max(0, Math.min(docCurrentPage - 2, docTotalPages - 5));
                    return start + i;
                  }).filter(p => p < docTotalPages) as p}
                    <button onclick={() => goToDocPage(p)}
                      class="px-3 py-1 text-xs rounded border transition-colors
                        {p === docCurrentPage ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}"
                    >{p + 1}</button>
                  {/each}
                  <button onclick={() => goToDocPage(docCurrentPage + 1)} disabled={docCurrentPage >= docTotalPages - 1}
                    class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700">Next</button>
                  <button onclick={() => goToDocPage(docTotalPages - 1)} disabled={docCurrentPage >= docTotalPages - 1}
                    class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700">Last</button>
                </div>
              </div>
            {:else}
              <div class="px-4 py-2 text-xs text-gray-400 border-t border-gray-200 dark:border-gray-700">
                {documents.length} document{documents.length !== 1 ? 's' : ''}
              </div>
            {/if}
          {/if}
        </div>
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
              Your tenant is currently using <span class="font-mono font-medium">{tenantSettings?.effectiveModel}</span>.
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

          <!-- Tenant Defaults — inline editable * wildcard (tenant_admin only) -->
          {#if isAdmin}
            <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <div class="flex items-center justify-between mb-1">
                <div>
                  <h3 class="text-base font-semibold text-gray-900 dark:text-white">Tenant Defaults</h3>
                  <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    Sampling parameters applied to all models for this tenant.
                    Changes affect only this tenant and are stored in DB.
                  </p>
                </div>
                {#if wildcardProfile}
                  {#if defaultsEditMode}
                    <div class="flex gap-2">
                      <button
                        onclick={() => { defaultsEditMode = false; syncDefaultsForm(wildcardProfile!); }}
                        class="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700"
                      >Cancel</button>
                      <button
                        onclick={saveDefaults}
                        disabled={defaultsSaving}
                        class="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                      >{defaultsSaving ? 'Saving…' : 'Save'}</button>
                    </div>
                  {:else}
                    <button
                      onclick={() => defaultsEditMode = true}
                      class="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700"
                    >Edit</button>
                  {/if}
                {/if}
              </div>

              {#if wildcardProfile}
                {#if defaultsEditMode}
                  <!-- Inline edit form — two-column Standard | Thinking -->
                  <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-6 text-xs">
                    <!-- Standard mode -->
                    <div>
                      <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-3">Standard mode</p>
                      <div class="grid grid-cols-2 gap-3">
                        {#each [
                          { key: 'temperature',      label: 'Temperature',       step: '0.01', min: '0', max: '2'  },
                          { key: 'topP',             label: 'Top P',             step: '0.01', min: '0', max: '1'  },
                          { key: 'maxTokens',        label: 'Max Tokens',        step: '1',    min: '1', max: ''   },
                          { key: 'frequencyPenalty', label: 'Freq Penalty',      step: '0.01', min: '-2', max: '2' },
                          { key: 'presencePenalty',  label: 'Presence Penalty',  step: '0.01', min: '-2', max: '2' },
                          { key: 'repetitionPenalty',label: 'Repetition Penalty',step: '0.01', min: '1', max: '2'  },
                          { key: 'topK',             label: 'Top K',             step: '1',    min: '0', max: ''   },
                          { key: 'minP',             label: 'Min P',             step: '0.01', min: '0', max: '1'  },
                        ] as f}
                          <div>
                            <label class="block text-gray-500 dark:text-gray-400 mb-1">{f.label} <span class="text-gray-400">(blank=inherit)</span></label>
                            <input
                              type="number"
                              step={f.step} min={f.min} max={f.max || undefined}
                              bind:value={defaultsForm[f.key as keyof typeof defaultsForm]}
                              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                            />
                          </div>
                        {/each}
                      </div>
                    </div>
                    <!-- Thinking mode -->
                    <div>
                      <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-3">Thinking mode</p>
                      <div class="grid grid-cols-2 gap-3">
                        {#each [
                          { key: 'thinkingTemperature',       label: 'Temperature',        step: '0.01', min: '0', max: '2'  },
                          { key: 'thinkingTopP',              label: 'Top P',              step: '0.01', min: '0', max: '1'  },
                          { key: 'thinkingMaxTokens',         label: 'Max Tokens',         step: '1',    min: '1', max: ''   },
                          { key: 'thinkingBudget',            label: 'Thinking Budget',    step: '1',    min: '1', max: ''   },
                          { key: 'thinkingFrequencyPenalty',  label: 'Freq Penalty',       step: '0.01', min: '-2', max: '2' },
                          { key: 'thinkingPresencePenalty',   label: 'Presence Penalty',   step: '0.01', min: '-2', max: '2' },
                          { key: 'thinkingRepetitionPenalty', label: 'Repetition Penalty', step: '0.01', min: '1', max: '2'  },
                          { key: 'thinkingTopK',              label: 'Top K',              step: '1',    min: '0', max: ''   },
                          { key: 'thinkingMinP',              label: 'Min P',              step: '0.01', min: '0', max: '1'  },
                        ] as f}
                          <div>
                            <label class="block text-gray-500 dark:text-gray-400 mb-1">{f.label} <span class="text-gray-400">(blank=inherit)</span></label>
                            <input
                              type="number"
                              step={f.step} min={f.min} max={f.max || undefined}
                              bind:value={defaultsForm[f.key as keyof typeof defaultsForm]}
                              class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-2 py-1.5 text-xs focus:ring-1 focus:ring-blue-500"
                            />
                          </div>
                        {/each}
                      </div>
                    </div>
                  </div>
                {:else}
                  <!-- Read-only display — two-column Standard | Thinking -->
                  <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-6 text-xs">
                    <div>
                      <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-3">Standard mode</p>
                      <div class="grid grid-cols-2 gap-2">
                        {#each [
                          { label: 'Temperature',        value: wildcardProfile.temperature },
                          { label: 'Top P',              value: wildcardProfile.topP },
                          { label: 'Max Tokens',         value: wildcardProfile.maxTokens },
                          { label: 'Freq Penalty',       value: wildcardProfile.frequencyPenalty },
                          { label: 'Presence Penalty',   value: wildcardProfile.presencePenalty },
                          { label: 'Repetition Penalty', value: wildcardProfile.repetitionPenalty },
                          { label: 'Top K',              value: wildcardProfile.topK },
                          { label: 'Min P',              value: wildcardProfile.minP },
                        ] as p}
                          <div class="bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2">
                            <p class="text-gray-400 dark:text-gray-500 mb-0.5">{p.label}</p>
                            <p class="font-mono font-semibold text-gray-900 dark:text-white">{p.value ?? '—'}</p>
                          </div>
                        {/each}
                      </div>
                    </div>
                    <div>
                      <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-3">Thinking mode</p>
                      <div class="grid grid-cols-2 gap-2">
                        {#each [
                          { label: 'Temperature',        value: wildcardProfile.thinkingTemperature },
                          { label: 'Top P',              value: wildcardProfile.thinkingTopP },
                          { label: 'Max Tokens',         value: wildcardProfile.thinkingMaxTokens },
                          { label: 'Thinking Budget',    value: wildcardProfile.thinkingBudget },
                          { label: 'Freq Penalty',       value: wildcardProfile.thinkingFrequencyPenalty },
                          { label: 'Presence Penalty',   value: wildcardProfile.thinkingPresencePenalty },
                          { label: 'Repetition Penalty', value: wildcardProfile.thinkingRepetitionPenalty },
                          { label: 'Top K',              value: wildcardProfile.thinkingTopK },
                          { label: 'Min P',              value: wildcardProfile.thinkingMinP },
                        ] as p}
                          <div class="bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2">
                            <p class="text-gray-400 dark:text-gray-500 mb-0.5">{p.label}</p>
                            <p class="font-mono font-semibold text-gray-900 dark:text-white">{p.value ?? '—'}</p>
                          </div>
                        {/each}
                      </div>
                    </div>
                  </div>
                {/if}
              {:else}
                <div class="mt-4 flex items-center gap-4">
                  <p class="text-sm text-gray-400">No defaults configured for this tenant.</p>
                  <button
                    onclick={initializeDefaults}
                    disabled={seedingDefaults}
                    class="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                  >{seedingDefaults ? 'Initializing…' : 'Initialize Defaults'}</button>
                </div>
              {/if}
            </div>
          {/if}

          <!-- Model-specific Overrides — non-wildcard patterns (tenant_admin only) -->
          {#if isAdmin}
            <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <div class="flex items-center justify-between mb-1">
                <h3 class="text-base font-semibold text-gray-900 dark:text-white">Model-specific Overrides</h3>
                <button
                  onclick={openCreateProfileModal}
                  class="px-3 py-1.5 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                >+ Add Override</button>
              </div>
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">
                Per-model pattern overrides that take precedence over tenant defaults (e.g. <span class="font-mono">qwen3*</span>).
                Blank fields = inherit from tenant defaults.
              </p>
              {#if specificProfiles.length === 0}
                <p class="text-sm text-gray-400">No model-specific overrides — all models use tenant defaults above.</p>
              {:else}
                <div class="overflow-x-auto">
                  <table class="w-full text-xs">
                    <thead class="border-b border-gray-200 dark:border-gray-700">
                      <tr>
                        <th class="text-left py-2 font-medium text-gray-600 dark:text-gray-400 pr-4">Pattern</th>
                        <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Temp</th>
                        <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think Temp</th>
                        <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think TopP</th>
                        <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">Think MaxTok</th>
                        <th class="text-right py-2 font-medium text-gray-600 dark:text-gray-400 pr-3">MaxTok</th>
                        <th class="py-2"></th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                      {#each specificProfiles as p}
                        <tr>
                          <td class="py-2 font-mono text-gray-900 dark:text-white pr-4">{p.modelPattern}</td>
                          <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.temperature ?? '—'}</td>
                          <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingTemperature ?? '—'}</td>
                          <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingTopP ?? '—'}</td>
                          <td class="py-2 text-right text-gray-700 dark:text-gray-300 pr-3">{p.thinkingMaxTokens ?? '—'}</td>
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
          {/if}

        </div>
      {/if}
    {/if}

    <!-- Analytics Tab -->
    {#if activeTab === 'analytics'}
      <div class="space-y-5">

        <!-- Platform admin: tenant selector -->
        {#if _isPlatformAdmin && allTenants.length > 0}
          <div class="flex items-center gap-3">
            <label class="text-sm text-gray-600 dark:text-gray-400 font-medium">Tenant:</label>
            <select
              bind:value={analyticsSelectedTenant}
              onchange={() => loadAnalytics()}
              class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-1 focus:ring-blue-500"
            >
              <option value="">All tenants</option>
              {#each allTenants as t}
                <option value={t.tenantId}>{t.name} ({t.tenantId})</option>
              {/each}
            </select>
            <a
              href="http://localhost:3002/d/docintel-analytics"
              target="_blank"
              rel="noopener noreferrer"
              class="ml-auto text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
            >
              Open in Grafana →
            </a>
          </div>
        {:else}
          <div class="flex justify-end">
            <a
              href="http://localhost:3002/d/docintel-analytics"
              target="_blank"
              rel="noopener noreferrer"
              class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
            >Open in Grafana →</a>
          </div>
        {/if}

        {#if analyticsLoading}
          <div class="text-center py-12 text-gray-400">Loading analytics…</div>
        {:else}

          <!-- Summary stat cards -->
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
            {#each [
              { label: 'Total Queries', value: querySummary?.totalQueries?.toLocaleString() ?? '—' },
              { label: 'Avg Latency', value: querySummary ? `${Math.round(querySummary.avgLatencyMs)}ms` : '—' },
              { label: 'Cache Hit Rate', value: querySummary ? `${(querySummary.cacheHitRate * 100).toFixed(1)}%` : '—' },
              { label: 'Like Rate', value: feedbackSummary ? `${(feedbackSummary.likeRate * 100).toFixed(1)}%` : '—' },
            ] as card}
              <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <p class="text-xs text-gray-500 dark:text-gray-400">{card.label}</p>
                <p class="text-2xl font-bold text-gray-900 dark:text-white mt-1">{card.value}</p>
              </div>
            {/each}
          </div>

          <!-- Queries per day sparkline -->
          {#if queryTimeseries.length > 0}
            {@const maxCount = Math.max(...queryTimeseries.map(p => p.count), 1)}
            {@const maxLatency = Math.max(...queryTimeseries.map(p => p.avg_latency_ms), 1)}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <!-- Queries/day -->
              <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Queries / day (last 30d)</p>
                <svg viewBox="0 0 {queryTimeseries.length * 10} 60" class="w-full h-16" preserveAspectRatio="none">
                  {#each queryTimeseries as pt, i}
                    {@const barH = Math.round((pt.count / maxCount) * 56)}
                    <rect
                      x={i * 10 + 1}
                      y={60 - barH}
                      width="8"
                      height={barH}
                      class="fill-blue-400 dark:fill-blue-500"
                      rx="1"
                    />
                  {/each}
                </svg>
                <div class="flex justify-between text-xs text-gray-400 mt-1">
                  <span>{queryTimeseries[0]?.ts?.slice(0, 10) ?? ''}</span>
                  <span>{queryTimeseries[queryTimeseries.length - 1]?.ts?.slice(0, 10) ?? ''}</span>
                </div>
              </div>
              <!-- Avg latency/day -->
              <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Avg latency ms / day (last 30d)</p>
                <svg viewBox="0 0 {queryTimeseries.length * 10} 60" class="w-full h-16" preserveAspectRatio="none">
                  {#each queryTimeseries as pt, i}
                    {@const barH = Math.round((pt.avg_latency_ms / maxLatency) * 56)}
                    <rect
                      x={i * 10 + 1}
                      y={60 - barH}
                      width="8"
                      height={barH}
                      class="fill-indigo-400 dark:fill-indigo-500"
                      rx="1"
                    />
                  {/each}
                </svg>
                <div class="flex justify-between text-xs text-gray-400 mt-1">
                  <span>{queryTimeseries[0]?.ts?.slice(0, 10) ?? ''}</span>
                  <span>{queryTimeseries[queryTimeseries.length - 1]?.ts?.slice(0, 10) ?? ''}</span>
                </div>
              </div>
            </div>
          {/if}

          <!-- Queries by model + Feedback -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <!-- By model -->
            <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Queries by model</p>
              {#if modelStats.length === 0}
                <p class="text-sm text-gray-400">No data yet.</p>
              {:else}
                {@const maxModel = Math.max(...modelStats.map(m => m.count), 1)}
                <div class="space-y-2">
                  {#each modelStats.sort((a, b) => b.count - a.count) as ms}
                    <div>
                      <div class="flex justify-between text-xs mb-0.5">
                        <span class="text-gray-700 dark:text-gray-300 font-mono truncate max-w-[60%]">{ms.model}</span>
                        <span class="text-gray-500 dark:text-gray-400">{ms.count} · {Math.round(ms.avg_latency_ms)}ms avg</span>
                      </div>
                      <div class="w-full bg-gray-100 dark:bg-gray-700 rounded-full h-1.5">
                        <div class="bg-blue-500 h-1.5 rounded-full" style="width: {Math.round(ms.count / maxModel * 100)}%"></div>
                      </div>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>

            <!-- Feedback breakdown -->
            <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-3">Feedback</p>
              {#if feedbackSummary}
                <div class="grid grid-cols-3 gap-3 mb-4">
                  {#each [
                    { label: 'Total', value: feedbackSummary.totalFeedback, color: 'text-gray-700 dark:text-gray-200' },
                    { label: 'Likes', value: feedbackSummary.likes, color: 'text-green-600 dark:text-green-400' },
                    { label: 'Dislikes', value: feedbackSummary.dislikes, color: 'text-red-600 dark:text-red-400' },
                  ] as fb}
                    <div class="text-center">
                      <div class="text-2xl font-bold {fb.color}">{fb.value}</div>
                      <div class="text-xs text-gray-400">{fb.label}</div>
                    </div>
                  {/each}
                </div>
                {#if feedbackSummary.totalFeedback > 0}
                  <div class="w-full bg-gray-100 dark:bg-gray-700 rounded-full h-2 overflow-hidden">
                    <div class="bg-green-500 h-2 rounded-full transition-all"
                      style="width: {Math.round(feedbackSummary.likeRate * 100)}%"></div>
                  </div>
                  <p class="text-xs text-gray-400 mt-1 text-center">{(feedbackSummary.likeRate * 100).toFixed(1)}% positive</p>
                {/if}
              {:else}
                <p class="text-sm text-gray-400">No feedback yet.</p>
              {/if}

              <!-- Feedback timeseries mini bars -->
              {#if feedbackTimeseries.length > 0}
                {@const maxFb = Math.max(...feedbackTimeseries.map(p => p.likes + p.dislikes), 1)}
                <div class="mt-4">
                  <p class="text-xs text-gray-400 mb-2">Likes/dislikes per day</p>
                  <svg viewBox="0 0 {feedbackTimeseries.length * 10} 40" class="w-full h-10" preserveAspectRatio="none">
                    {#each feedbackTimeseries as pt, i}
                      {@const likeH = Math.round((pt.likes / maxFb) * 36)}
                      {@const disH = Math.round((pt.dislikes / maxFb) * 36)}
                      <rect x={i * 10 + 1} y={40 - likeH} width="4" height={likeH} class="fill-green-400" rx="1" />
                      <rect x={i * 10 + 5} y={40 - disH} width="4" height={disH} class="fill-red-400" rx="1" />
                    {/each}
                  </svg>
                </div>
              {/if}
            </div>
          </div>

        {/if}
      </div>
    {/if}

    <!-- Preferences Tab -->
    {#if activeTab === 'preferences'}
      {#if preferencesLoading}
        <div class="text-center py-12 text-gray-400">Loading...</div>
      {:else}
        <div class="space-y-6">
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h3 class="text-base font-semibold text-gray-900 dark:text-white mb-1">Personal Preferences</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-6">
              These settings apply only to your account — they do not affect other users in your tenant.
            </p>

            <!-- Thinking Mode -->
            <div class="flex items-start justify-between gap-4">
              <div class="flex-1">
                <p class="text-sm font-medium text-gray-900 dark:text-white mb-1">Thinking Mode</p>
                <p class="text-sm text-gray-500 dark:text-gray-400">
                  When enabled, the model reasons step-by-step before answering (shown in a collapsible panel).
                  Best for complex multi-step analysis. For straightforward factual retrieval, keeping this
                  <span class="font-medium">off</span> reduces latency.
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

{#if profileModalOpen}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onclick={() => { profileModalOpen = false; }}>
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
    <div class="bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6 space-y-4 max-h-[90vh] overflow-y-auto" onclick={(e) => e.stopPropagation()}>
      <h2 class="text-lg font-semibold text-gray-900 dark:text-white">
        {profileModalMode === 'create' ? 'Add Sampling Override' : 'Edit Sampling Override'}
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
          <input bind:value={profileForm.displayName} placeholder="Optional label"
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500" />
        </div>

        <p class="col-span-2 text-xs text-gray-500 dark:text-gray-400 font-medium pt-1">Standard mode</p>

        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Temperature <span class="text-gray-400">(blank = inherit)</span></label>
          <input type="number" step="0.01" min="0" max="2" bind:value={profileForm.temperature} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Top P</label>
          <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.topP} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Max Tokens</label>
          <input type="number" step="1" min="1" bind:value={profileForm.maxTokens} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Frequency Penalty</label>
          <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.frequencyPenalty} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Presence Penalty</label>
          <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.presencePenalty} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Repetition Penalty</label>
          <input type="number" step="0.01" min="1" max="2" bind:value={profileForm.repetitionPenalty} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Top K</label>
          <input type="number" step="1" min="0" bind:value={profileForm.topK} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Min P</label>
          <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.minP} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>

        <p class="col-span-2 text-xs text-gray-500 dark:text-gray-400 font-medium pt-1">Thinking mode</p>

        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Temperature</label>
          <input type="number" step="0.01" min="0" max="2" bind:value={profileForm.thinkingTemperature} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Top P</label>
          <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.thinkingTopP} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Max Tokens</label>
          <input type="number" step="1" min="1" bind:value={profileForm.thinkingMaxTokens} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Thinking Budget</label>
          <input type="number" step="1" min="1" bind:value={profileForm.thinkingBudget} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Frequency Penalty</label>
          <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.thinkingFrequencyPenalty} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Presence Penalty</label>
          <input type="number" step="0.01" min="-2" max="2" bind:value={profileForm.thinkingPresencePenalty} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Repetition Penalty</label>
          <input type="number" step="0.01" min="1" max="2" bind:value={profileForm.thinkingRepetitionPenalty} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Top K</label>
          <input type="number" step="1" min="0" bind:value={profileForm.thinkingTopK} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Min P</label>
          <input type="number" step="0.01" min="0" max="1" bind:value={profileForm.thinkingMinP} placeholder=""
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white px-3 py-2 text-sm" />
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
