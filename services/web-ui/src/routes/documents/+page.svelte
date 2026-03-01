<script lang="ts">
  import { env } from '$env/dynamic/public';
  import { getAuthHeaders, getTenantId } from '$lib/auth';
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

  interface Document {
    id: string;
    filename: string;
    contentType: string | null;
    fileSize: number;
    chunkCount: number;
    status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
    createdAt: string;
    updatedAt: string;
  }

  interface DatasetInfo {
    key: string;
    name: string;
    domain: string;
    description: string;
  }

  interface LoadingProgress {
    phase: 'downloading' | 'chunking' | 'embedding' | 'indexing' | 'done';
    message: string;
  }

  interface VectorStats {
    total_vectors: number;
    collections: Record<string, number>;
    tenant_stats: Record<string, number>;
  }

  // Pagination
  let currentPage = $state(0);
  let pageSize = $state(20);
  let totalPages = $state(0);
  let totalElements = $state(0);

  // State
  let documents: Document[] = $state([]);
  let vectorStats: VectorStats | null = $state(null);
  let isLoading = $state(true);
  let isUploading = $state(false);
  let isLoadingDatasets = $state(false);
  let isLoadingStats = $state(false);
  let loadingProgress: LoadingProgress | null = $state(null);
  let error = $state<string | null>(null);
  let success = $state<string | null>(null);
  let selectedFile = $state<File | null>(null);
  let selectedDomain = $state('auto');
  let availableDatasets: DatasetInfo[] = $state([]);
  let selectedDatasets: string[] = $state([]);
  let samplesPerDataset = $state(100);

  const API_BASE = env.PUBLIC_API_URL || 'http://localhost:8080';

  // Confirm dialog state
  let confirmOpen = $state(false);
  let confirmDocId: string | null = $state(null);

  const DOMAIN_OPTIONS = [
    { value: 'auto', label: 'Auto-detect', description: 'AI classifies domain' },
    { value: 'technical', label: 'Technical', description: 'Tech docs' },
    { value: 'hr_policy', label: 'HR Policy', description: 'HR policies' },
    { value: 'contracts', label: 'Contracts', description: 'Legal contracts' },
    { value: 'general', label: 'General', description: 'General docs' },
  ];

  const PHASE_LABELS: Record<string, string> = {
    downloading: '📥 Downloading from HuggingFace...',
    chunking: '✂️ Chunking documents...',
    embedding: '🧠 Generating embeddings...',
    indexing: '📊 Indexing to vector database...',
    done: '✅ Complete!'
  };

  async function loadDocuments(page: number = 0) {
    isLoading = true;
    try {
      const params = new URLSearchParams({
        tenant_id: getTenantId(),
        page: String(page),
        size: String(pageSize),
        sort: 'createdAt,desc',
      });
      const response = await fetch(`${API_BASE}/api/v1/documents?${params}`, {
        headers: { ...getAuthHeaders() },
      });
      if (response.ok) {
        const data = await response.json();
        // Spring Page response
        if (data.content) {
          documents = data.content;
          totalPages = data.totalPages ?? 1;
          totalElements = data.totalElements ?? data.content.length;
          currentPage = data.number ?? page;
        } else {
          // Fallback: plain array
          documents = Array.isArray(data) ? data : [];
          totalPages = 1;
          totalElements = documents.length;
        }
      } else {
        documents = [];
      }
    } catch (e) {
      documents = [];
    } finally {
      isLoading = false;
    }
  }

  function goToPage(page: number) {
    if (page >= 0 && page < totalPages) {
      loadDocuments(page);
    }
  }

  async function loadVectorStats() {
    isLoadingStats = true;
    try {
      const response = await fetch(`${API_BASE}/api/v1/vector-stats`, {
        headers: { ...getAuthHeaders() },
      });
      if (response.ok) {
        vectorStats = await response.json();
        console.log('Vector stats loaded:', vectorStats);
      } else {
        console.error('Vector stats failed:', response.status);
        vectorStats = null;
      }
    } catch (e) {
      console.error('Vector stats error:', e);
      vectorStats = null;
    } finally {
      isLoadingStats = false;
    }
  }

  async function loadAvailableDatasets() {
    try {
      const response = await fetch(`${API_BASE}/api/v1/sample-datasets`);
      if (response.ok) {
        const data = await response.json();
        availableDatasets = data.available_datasets || [];
      } else {
        throw new Error('Failed to load');
      }
    } catch (e) {
      availableDatasets = [
        { key: 'techqa', name: 'TechQA', domain: 'technical', description: 'Technical documentation Q&A' },
        { key: 'hr_policies', name: 'HR Policies', domain: 'hr_policy', description: 'HR policy Q&A pairs' },
        { key: 'cuad', name: 'Legal Cases', domain: 'contracts', description: 'European Court legal cases' },
      ];
    }
  }

  async function uploadDocument() {
    if (!selectedFile || isUploading) return;

    isUploading = true;
    error = null;
    success = null;

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('domain', selectedDomain);

      const response = await fetch(`${API_BASE}/api/v1/documents`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
        body: formData,
      });

      if (!response.ok) throw new Error(`Upload failed: HTTP ${response.status}`);

      const result = await response.json();
      success = `Uploaded "${result.filename}" successfully`;
      selectedFile = null;
      selectedDomain = 'auto';
      
      const fileInput = document.getElementById('file-upload') as HTMLInputElement;
      if (fileInput) fileInput.value = '';
      
      await refreshAll();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Upload failed';
    } finally {
      isUploading = false;
    }
  }

  function selectAllDatasets() {
    if (selectedDatasets.length === availableDatasets.length) {
      selectedDatasets = [];
    } else {
      selectedDatasets = availableDatasets.map(d => d.key);
    }
  }

  async function loadSampleDatasets() {
    if (selectedDatasets.length === 0 || isLoadingDatasets) return;

    isLoadingDatasets = true;
    error = null;
    success = null;
    
    loadingProgress = {
      phase: 'downloading',
      message: `Downloading ${selectedDatasets.length} dataset(s)...`
    };

    try {
      const response = await fetch(`${API_BASE}/api/v1/sample-datasets/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          datasets: selectedDatasets,
          samples_per_dataset: samplesPerDataset === -1 ? 100000 : samplesPerDataset,
          tenant_id: getTenantId(),
        }),
      });

      if (!response.ok) throw new Error(`Load failed: HTTP ${response.status}`);

      const result = await response.json();
      
      loadingProgress = { phase: 'done', message: 'Loading complete!' };
      success = `Loaded ${result.total_indexed} documents from ${result.loaded.length} dataset(s)`;
      selectedDatasets = [];
      
      // Refresh data after short delay
      setTimeout(async () => {
        await refreshAll();
        loadingProgress = null;
      }, 1500);
      
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load sample datasets';
      loadingProgress = null;
    } finally {
      isLoadingDatasets = false;
    }
  }

  function deleteDocument(id: string) {
    confirmDocId = id;
    confirmOpen = true;
  }

  async function doDeleteDocument() {
    if (!confirmDocId) return;
    const id = confirmDocId;
    confirmOpen = false;
    confirmDocId = null;
    try {
      const response = await fetch(`${API_BASE}/api/v1/documents/${id}`, {
        method: 'DELETE',
        headers: { ...getAuthHeaders() },
      });

      if (!response.ok && response.status !== 204) {
        throw new Error(`Delete failed: HTTP ${response.status}`);
      }

      await refreshAll();
      toast.success('Document deleted');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Delete failed';
      error = msg;
      toast.error(msg);
    }
  }

  async function refreshAll() {
    await Promise.all([loadDocuments(), loadVectorStats()]);
  }

  function toggleDataset(key: string) {
    if (selectedDatasets.includes(key)) {
      selectedDatasets = selectedDatasets.filter((d) => d !== key);
    } else {
      selectedDatasets = [...selectedDatasets, key];
    }
  }

  function formatFileSize(bytes: number): string {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function formatDate(dateStr: string): string {
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function getStatusColor(status: string): string {
    const colors: Record<string, string> = {
      COMPLETED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
      PROCESSING: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
      PENDING: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-400',
      FAILED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    };
    return colors[status] || colors.PENDING;
  }

  function getDomainColor(domain: string): string {
    const colors: Record<string, string> = {
      technical: 'text-blue-600 dark:text-blue-400',
      hr_policy: 'text-purple-600 dark:text-purple-400',
      contracts: 'text-orange-600 dark:text-orange-400',
      general: 'text-gray-600 dark:text-gray-400',
    };
    return colors[domain] || colors.general;
  }

  function handleFileChange(event: Event) {
    const target = event.target as HTMLInputElement;
    selectedFile = target.files?.[0] || null;
  }

  function getEstimatedTime(samples: number, datasets: number): string {
    const total = samples * datasets;
    if (total <= 50) return '~30 seconds';
    if (total <= 200) return '~1-2 minutes';
    if (total <= 500) return '~3-5 minutes';
    return '~10-15 minutes';
  }
  
  // Calculate total indexed
  function getTotalIndexed(): number {
    if (!vectorStats) return 0;
    return vectorStats.collections['documents'] || 0;
  }

  $effect(() => {
    loadDocuments();
    loadAvailableDatasets();
    loadVectorStats();
  });
</script>

<div class="h-full overflow-y-auto p-6">
  <div class="max-w-5xl mx-auto space-y-6">
    
    <!-- Page Header with Refresh -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Documents</h1>
        <p class="text-gray-500 dark:text-gray-400 mt-1">Upload documents or load sample datasets for testing</p>
      </div>
      <button 
        onclick={refreshAll}
        disabled={isLoading || isLoadingStats}
        class="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
      >
        <svg class="w-4 h-4 {isLoading || isLoadingStats ? 'animate-spin' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        Refresh
      </button>
    </div>
    
    <!-- Success/Error Messages -->
    {#if success}
      <div class="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4 flex items-center gap-3">
        <span class="text-green-600 dark:text-green-400">✓</span>
        <p class="text-green-700 dark:text-green-300 flex-1">{success}</p>
        <button onclick={() => success = null} class="text-green-500 hover:text-green-700 text-xl">&times;</button>
      </div>
    {/if}

    {#if error}
      <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 flex items-center gap-3">
        <span class="text-red-600 dark:text-red-400">!</span>
        <p class="text-red-700 dark:text-red-300 flex-1">{error}</p>
        <button onclick={() => error = null} class="text-red-500 hover:text-red-700 text-xl">&times;</button>
      </div>
    {/if}

    <!-- Vector Store Stats -->
    <div class="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-xl border border-blue-200 dark:border-blue-800 p-6">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-lg font-semibold text-gray-900 dark:text-white">Indexed Documents</h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Documents indexed in vector store (searchable)
          </p>
        </div>
        {#if isLoadingStats}
          <div class="w-5 h-5 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
        {/if}
      </div>
      
      {#if vectorStats && getTotalIndexed() > 0}
        <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div class="bg-white dark:bg-gray-800 rounded-lg p-4 text-center shadow-sm">
            <div class="text-3xl font-bold text-blue-600 dark:text-blue-400">
              {getTotalIndexed()}
            </div>
            <div class="text-sm text-gray-600 dark:text-gray-400">Total Chunks</div>
          </div>
          
          {#each Object.entries(vectorStats.tenant_stats) as [domain, count]}
            <div class="bg-white dark:bg-gray-800 rounded-lg p-4 text-center shadow-sm">
              <div class="text-3xl font-bold {getDomainColor(domain)}">
                {count}
              </div>
              <div class="text-sm text-gray-600 dark:text-gray-400 capitalize">{domain.replace('_', ' ')}</div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="bg-white dark:bg-gray-800 rounded-lg p-6 text-center">
          <p class="text-gray-500 dark:text-gray-400">
            No documents indexed yet. Load sample datasets or upload documents below.
          </p>
        </div>
      {/if}
    </div>

    <!-- Sample Datasets Section -->
    <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm p-6">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-lg font-semibold text-gray-900 dark:text-white">Load Sample Datasets</h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Load pre-built documents from HuggingFace
          </p>
        </div>
        {#if selectedDatasets.length > 0}
          <span class="text-sm text-blue-600 dark:text-blue-400">
            {selectedDatasets.length} selected • Est. {getEstimatedTime(samplesPerDataset, selectedDatasets.length)}
          </span>
        {/if}
      </div>

      <!-- Loading Progress -->
      {#if loadingProgress}
        <div class="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
          <div class="flex items-center gap-3">
            {#if loadingProgress.phase !== 'done'}
              <div class="w-5 h-5 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
            {:else}
              <span class="text-green-500 text-xl">✓</span>
            {/if}
            <span class="font-medium text-blue-800 dark:text-blue-300">
              {PHASE_LABELS[loadingProgress.phase]}
            </span>
          </div>
        </div>
      {/if}

      <!-- Limitation notice -->
      <div class="mb-4 p-3 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800 text-sm text-amber-700 dark:text-amber-400">
        Sample datasets are sourced from HuggingFace for demo purposes. They may not cover all topics — answers depend on the content available in the loaded samples.
      </div>

      <div class="flex items-center justify-between mb-3">
        <button
          onclick={selectAllDatasets}
          disabled={isLoadingDatasets}
          class="text-sm text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
        >
          {selectedDatasets.length === availableDatasets.length ? 'Deselect All' : 'Select All'}
        </button>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        {#each availableDatasets as dataset}
          <label
            class="flex items-start gap-3 p-4 border rounded-lg cursor-pointer transition-all 
              {selectedDatasets.includes(dataset.key)
                ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-500'
                : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'}"
          >
            <input
              type="checkbox"
              checked={selectedDatasets.includes(dataset.key)}
              onchange={() => toggleDataset(dataset.key)}
              disabled={isLoadingDatasets}
              class="mt-1 h-4 w-4 text-blue-600 rounded"
            />
            <div>
              <span class="font-medium text-gray-900 dark:text-white">{dataset.name}</span>
              <span class="ml-2 px-2 py-0.5 text-xs rounded-full bg-gray-100 dark:bg-gray-700 {getDomainColor(dataset.domain)}">
                {dataset.domain}
              </span>
              <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{dataset.description}</p>
            </div>
          </label>
        {/each}
      </div>

      <div class="flex flex-wrap items-center gap-4">
        <div class="flex items-center gap-2">
          <label for="samples" class="text-sm text-gray-600 dark:text-gray-400">Samples:</label>
          <select
            id="samples"
            bind:value={samplesPerDataset}
            disabled={isLoadingDatasets}
            class="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-1.5 text-sm 
              bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
          >
            <option value={10}>10</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={250}>250</option>
            <option value={500}>500</option>
            <option value={1000}>1000</option>
            <option value={-1}>All available</option>
          </select>
        </div>
        <button
          onclick={loadSampleDatasets}
          disabled={selectedDatasets.length === 0 || isLoadingDatasets}
          class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 
            disabled:opacity-50 disabled:cursor-not-allowed transition-colors
            flex items-center gap-2"
        >
          {#if isLoadingDatasets}
            <div class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            Loading...
          {:else}
            Load Selected
          {/if}
        </button>
      </div>
    </div>

    <!-- Upload Section -->
    <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm p-6">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-white mb-4">Upload Your Document</h2>

      <div class="flex flex-wrap gap-2 mb-4">
        {#each DOMAIN_OPTIONS as option}
          <label
            class="flex items-center gap-2 px-3 py-2 border rounded-lg cursor-pointer transition-all text-sm
              {selectedDomain === option.value
                ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-500'
                : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'}"
          >
            <input
              type="radio"
              name="domain"
              value={option.value}
              checked={selectedDomain === option.value}
              onchange={() => selectedDomain = option.value}
              class="h-3 w-3 text-blue-600"
            />
            <span class="text-gray-900 dark:text-white">{option.label}</span>
          </label>
        {/each}
      </div>

      <div class="flex items-center gap-4">
        <input
          id="file-upload"
          type="file"
          onchange={handleFileChange}
          disabled={isUploading}
          accept=".txt,.pdf,.docx,.md"
          class="flex-1 text-sm text-gray-500 dark:text-gray-400
            file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0
            file:text-sm file:font-medium file:bg-blue-50 file:text-blue-700
            dark:file:bg-blue-900/30 dark:file:text-blue-400
            hover:file:bg-blue-100 file:cursor-pointer"
        />
        <button
          onclick={uploadDocument}
          disabled={!selectedFile || isUploading}
          class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 
            disabled:opacity-50 disabled:cursor-not-allowed transition-colors
            flex items-center gap-2"
        >
          {#if isUploading}
            <div class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
          {/if}
          Upload
        </button>
      </div>
    </div>

    <!-- Uploaded Documents List -->
    <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm p-6">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-white mb-4">
        Uploaded Documents
        {#if documents.length > 0}
          <span class="text-sm font-normal text-gray-500 dark:text-gray-400">({documents.length})</span>
        {/if}
      </h2>

      {#if isLoading}
        <div class="flex items-center justify-center py-12">
          <div class="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
        </div>
      {:else if documents.length === 0}
        <div class="text-center py-8 text-gray-500 dark:text-gray-400">
          <p>No uploaded documents yet.</p>
          <p class="text-sm mt-1">Upload a document above or use sample datasets.</p>
        </div>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="border-b border-gray-200 dark:border-gray-700">
                <th class="text-left py-3 px-4 text-sm font-medium text-gray-600 dark:text-gray-400">Filename</th>
                <th class="text-left py-3 px-4 text-sm font-medium text-gray-600 dark:text-gray-400">Size</th>
                <th class="text-left py-3 px-4 text-sm font-medium text-gray-600 dark:text-gray-400">Chunks</th>
                <th class="text-left py-3 px-4 text-sm font-medium text-gray-600 dark:text-gray-400">Status</th>
                <th class="text-left py-3 px-4 text-sm font-medium text-gray-600 dark:text-gray-400">Created</th>
                <th class="text-right py-3 px-4 text-sm font-medium text-gray-600 dark:text-gray-400">Actions</th>
              </tr>
            </thead>
            <tbody>
              {#each documents as doc}
                <tr class="border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30">
                  <td class="py-3 px-4">
                    <span class="text-gray-900 dark:text-white font-medium">{doc.filename}</span>
                  </td>
                  <td class="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">
                    {formatFileSize(doc.fileSize)}
                  </td>
                  <td class="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">
                    {doc.chunkCount}
                  </td>
                  <td class="py-3 px-4">
                    <span class="px-2 py-1 text-xs rounded-full {getStatusColor(doc.status)}">
                      {doc.status}
                    </span>
                  </td>
                  <td class="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">
                    {formatDate(doc.createdAt)}
                  </td>
                  <td class="py-3 px-4 text-right">
                    <button
                      onclick={() => deleteDocument(doc.id)}
                      class="text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 text-sm"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        
        <!-- Pagination -->
        {#if totalPages > 1}
          <div class="flex items-center justify-between mt-4 px-4 py-3 border-t border-gray-200 dark:border-gray-700">
            <p class="text-sm text-gray-500 dark:text-gray-400">
              Showing {currentPage * pageSize + 1}-{Math.min((currentPage + 1) * pageSize, totalElements)} of {totalElements}
            </p>
            <div class="flex items-center gap-1">
              <button
                onclick={() => goToPage(0)}
                disabled={currentPage === 0}
                class="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600
                  disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >First</button>
              <button
                onclick={() => goToPage(currentPage - 1)}
                disabled={currentPage === 0}
                class="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600
                  disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >Prev</button>
              
              {#each Array.from({length: Math.min(5, totalPages)}, (_, i) => {
                const start = Math.max(0, Math.min(currentPage - 2, totalPages - 5));
                return start + i;
              }).filter(p => p < totalPages) as page}
                <button
                  onclick={() => goToPage(page)}
                  class="px-3 py-1 text-sm rounded border transition-colors
                    {page === currentPage 
                      ? 'bg-blue-600 text-white border-blue-600' 
                      : 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}"
                >{page + 1}</button>
              {/each}
              
              <button
                onclick={() => goToPage(currentPage + 1)}
                disabled={currentPage >= totalPages - 1}
                class="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600
                  disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >Next</button>
              <button
                onclick={() => goToPage(totalPages - 1)}
                disabled={currentPage >= totalPages - 1}
                class="px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-600
                  disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >Last</button>
            </div>
          </div>
        {:else if totalElements > 0}
          <div class="px-4 py-2 text-sm text-gray-400 dark:text-gray-500 border-t border-gray-200 dark:border-gray-700">
            {totalElements} document{totalElements !== 1 ? 's' : ''}
          </div>
        {/if}
      {/if}
    </div>
  </div>
</div>

<ConfirmDialog
  open={confirmOpen}
  title="Delete document?"
  message="This document and all its chunks will be permanently deleted."
  confirmLabel="Delete"
  dangerous={true}
  onconfirm={doDeleteDocument}
  oncancel={() => { confirmOpen = false; confirmDocId = null; }}
/>
