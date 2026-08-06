<script lang="ts">
  import { onMount } from 'svelte';
  import { apiFetch } from '$lib/api';
  import { getAuthState, authStore } from '$lib/auth';
  import { toast } from 'svelte-sonner';

  let _authState = $state(getAuthState());
  authStore.subscribe((s) => { _authState = s; });
  const isPlatformAdmin = $derived(_authState.user?.role === 'platform_admin');

  interface FeedbackItem {
    query_id: string;
    tenant_id: string;
    user_id: string;
    liked: boolean | null;
    comment: string | null;
    query_text: string;
    answer_text: string;
    sources_json: string;
    created_at: string;
    trace_id: string;
    trace_url: string | null;
  }

  let filter: 'all' | 'liked' | 'disliked' = $state('disliked');
  let page = $state(0);
  const pageSize = 20;
  let loading = $state(true);
  let items: FeedbackItem[] = $state([]);
  let total = $state(0);
  let expandedId: string | null = $state(null);
  let allTenants: { tenantId: string; name: string }[] = $state([]);
  let selectedTenant = $state('');

  async function fetchJson(url: string) {
    const res = await apiFetch(url);
    return res.ok ? res.json() : null;
  }

  function sourcesFor(item: FeedbackItem): { title?: string; source?: string }[] {
    if (!item.sources_json) return [];
    try {
      const parsed = JSON.parse(item.sources_json);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  async function load() {
    loading = true;
    try {
      const likedParam = filter === 'liked' ? '&liked=true' : filter === 'disliked' ? '&liked=false' : '';
      const tenantParam = selectedTenant ? `&tenant_id=${encodeURIComponent(selectedTenant)}` : '';
      const data = await fetchJson(
        `/api/v1/analytics/feedback?page=${page}&page_size=${pageSize}${likedParam}${tenantParam}`,
      );
      items = data?.items ?? [];
      total = data?.total ?? 0;
    } catch (e) {
      toast.error(`Failed to load feedback: ${e}`);
    }
    loading = false;
  }

  function switchFilter(f: typeof filter) {
    filter = f;
    page = 0;
    expandedId = null;
    load();
  }

  function changePage(delta: number) {
    const next = page + delta;
    if (next < 0 || next * pageSize >= total) return;
    page = next;
    expandedId = null;
    load();
  }

  onMount(async () => {
    if (isPlatformAdmin) {
      const data = await fetchJson('/api/v1/tenants');
      allTenants = data ?? [];
    }
    await load();
  });
</script>

<div class="space-y-4">
  <div class="flex items-center gap-3 flex-wrap">
    <div class="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
      {#each [
        { id: 'disliked', label: 'Thumbs down' },
        { id: 'liked', label: 'Thumbs up' },
        { id: 'all', label: 'All' },
      ] as f}
        <button
          onclick={() => switchFilter(f.id as typeof filter)}
          class="px-3 py-1 text-xs font-medium rounded-md transition-colors
            {filter === f.id ? 'bg-white dark:bg-gray-700 shadow text-gray-900 dark:text-white' : 'text-gray-500 dark:text-gray-400'}"
        >{f.label}</button>
      {/each}
    </div>

    {#if isPlatformAdmin && allTenants.length > 0}
      <select
        bind:value={selectedTenant}
        onchange={() => { page = 0; load(); }}
        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
          bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-1 focus:ring-blue-500"
      >
        <option value="">All tenants</option>
        {#each allTenants as t}
          <option value={t.tenantId}>{t.name} ({t.tenantId})</option>
        {/each}
      </select>
    {/if}

    <span class="text-xs text-gray-400 ml-auto">{total} item{total === 1 ? '' : 's'}</span>
  </div>

  {#if loading}
    <div class="text-center py-12 text-gray-400">Loading feedback…</div>
  {:else if items.length === 0}
    <p class="text-sm text-gray-400 py-12 text-center">No feedback in this view.</p>
  {:else}
    <div class="space-y-2">
      {#each items as item}
        <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <button
            onclick={() => (expandedId = expandedId === item.query_id ? null : item.query_id)}
            class="w-full flex items-center gap-3 p-3 text-left hover:bg-gray-50 dark:hover:bg-gray-700/40 transition-colors"
          >
            <span class="text-lg shrink-0">{item.liked ? '👍' : item.liked === false ? '👎' : '—'}</span>
            <span class="text-sm text-gray-800 dark:text-gray-200 truncate flex-1">{item.query_text || '(no query text captured)'}</span>
            <span class="text-xs text-gray-400 shrink-0">{new Date(item.created_at).toLocaleString()}</span>
          </button>

          {#if expandedId === item.query_id}
            <div class="border-t border-gray-100 dark:border-gray-700 p-4 space-y-3 text-sm">
              <div>
                <p class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Query</p>
                <p class="text-gray-800 dark:text-gray-200">{item.query_text || '—'}</p>
              </div>
              <div>
                <p class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Answer</p>
                <p class="text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{item.answer_text || '—'}</p>
              </div>
              {#if item.comment}
                <div>
                  <p class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Comment</p>
                  <p class="text-gray-800 dark:text-gray-200">{item.comment}</p>
                </div>
              {/if}
              {#if sourcesFor(item).length > 0}
                <div>
                  <p class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Sources</p>
                  <ul class="list-disc list-inside text-gray-600 dark:text-gray-400 space-y-0.5">
                    {#each sourcesFor(item) as src}
                      <li>{src.title ?? src.source ?? JSON.stringify(src)}</li>
                    {/each}
                  </ul>
                </div>
              {/if}
              <div class="flex items-center justify-between pt-1">
                <span class="text-xs text-gray-400 font-mono">{item.tenant_id} · {item.user_id}</span>
                {#if item.trace_url}
                  <a
                    href={item.trace_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    class="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    View trace in Langfuse ↗
                  </a>
                {:else}
                  <span class="text-xs text-gray-400">No trace captured</span>
                {/if}
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>

    <div class="flex items-center justify-between pt-2">
      <button
        onclick={() => changePage(-1)}
        disabled={page === 0}
        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 disabled:opacity-40"
      >Previous</button>
      <span class="text-xs text-gray-400">Page {page + 1} of {Math.max(1, Math.ceil(total / pageSize))}</span>
      <button
        onclick={() => changePage(1)}
        disabled={(page + 1) * pageSize >= total}
        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 disabled:opacity-40"
      >Next</button>
    </div>
  {/if}
</div>
