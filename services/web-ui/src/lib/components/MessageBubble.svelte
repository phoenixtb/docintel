<script lang="ts">
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  interface Source {
    ref_id?: number;
    document_id: string;
    filename: string;
    section?: string;
    chunk_index?: number;
    score: number;
    content?: string;
  }

  interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    thinking?: string;
    sources?: Source[];
    liked?: boolean | null;
    queryId?: string;
  }

  let {
    message,
    onRegenerate,
    onFeedback,
  }: {
    message: Message;
    onRegenerate: () => void;
    onFeedback: (liked: boolean) => void;
  } = $props();

  let copied = $state(false);
  let expandedSources = $state<Set<number>>(new Set());

  marked.setOptions({ breaks: true, gfm: true });

  function renderMarkdown(text: string): string {
    const raw = marked.parse(text) as string;
    // Replace [N] with clickable ref badges. Must happen before DOMPurify so
    // data-ref is in ALLOWED_ATTR and survives sanitization.
    const withBadges = raw.replace(/\[(\d+)\]/g, (_, n) =>
      `<sup class="inline-ref" data-ref="${n}">${n}</sup>`
    );
    return DOMPurify.sanitize(withBadges, {
      ALLOWED_TAGS: ['p','ul','ol','li','strong','em','code','pre','blockquote',
                     'h1','h2','h3','h4','h5','h6','br','a','table','thead',
                     'tbody','tr','th','td','hr','sup','span'],
      ALLOWED_ATTR: ['href','title','class','id','target','rel','data-ref'],
    });
  }

  async function copyAnswer() {
    await navigator.clipboard.writeText(message.content);
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }

  function toggleSource(idx: number) {
    const next = new Set(expandedSources);
    next.has(idx) ? next.delete(idx) : next.add(idx);
    expandedSources = next;
  }

  function handleProseClick(event: MouseEvent) {
    const badge = (event.target as HTMLElement).closest('[data-ref]');
    if (!badge) return;
    const refId = parseInt(badge.getAttribute('data-ref') ?? '0', 10);
    if (!refId) return;

    // Expand the source card
    if (!expandedSources.has(refId)) {
      const next = new Set(expandedSources);
      next.add(refId);
      expandedSources = next;
    }

    // Scroll to it after Svelte updates the DOM
    requestAnimationFrame(() => {
      const card = document.querySelector(
        `[data-message-id="${message.id}"] [data-ref-id="${refId}"]`
      );
      card?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      card?.classList.add('ring-2', 'ring-blue-400');
      setTimeout(() => card?.classList.remove('ring-2', 'ring-blue-400'), 1200);
    });
  }

  function scoreColor(score: number): string {
    if (score >= 0.8) return 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300';
    if (score >= 0.5) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300';
    return 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400';
  }
</script>

<style>
  /* Scoped badge styles — Tailwind purges dynamic class strings injected via @html */
  :global(.inline-ref) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin: 0 1px;
    padding: 0 5px;
    border-radius: 9999px;
    background-color: rgb(219 234 254); /* blue-100 */
    color: rgb(29 78 216);              /* blue-700 */
    font-size: 0.7rem;
    font-weight: 600;
    line-height: 1.4;
    cursor: pointer;
    vertical-align: super;
    transition: background-color 0.15s;
    text-decoration: none;
  }
  :global(.dark .inline-ref) {
    background-color: rgb(30 58 138 / 0.6); /* blue-900/60 */
    color: rgb(147 197 253);                 /* blue-300 */
  }
  :global(.inline-ref:hover) {
    background-color: rgb(191 219 254); /* blue-200 */
  }
  :global(.dark .inline-ref:hover) {
    background-color: rgb(30 64 175 / 0.5);
  }
</style>

<div class="group flex justify-start">
  <div class="max-w-3xl w-full space-y-2">

    <!-- ── Thinking section (collapsible) ─────────────────────────── -->
    {#if message.thinking}
      <details class="rounded-lg border border-dashed border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900/40 text-xs">
        <summary class="flex items-center gap-2 px-3 py-2 cursor-pointer select-none text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 list-none">
          <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          <span class="font-medium">Thinking</span>
          <span class="ml-auto text-gray-400">(click to expand)</span>
        </summary>
        <pre class="px-3 pb-3 pt-1 font-mono text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap overflow-x-auto leading-relaxed">{message.thinking}</pre>
      </details>
    {/if}

    <!-- ── Answer bubble ──────────────────────────────────────────── -->
    <div class="px-4 py-3 rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm text-gray-900 dark:text-gray-100">
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div
        role="presentation"
        class="prose prose-sm dark:prose-invert max-w-none
                prose-p:my-1.5 prose-headings:mt-3 prose-headings:mb-1
                prose-code:bg-gray-100 prose-code:dark:bg-gray-700 prose-code:px-1 prose-code:rounded
                prose-pre:bg-gray-100 prose-pre:dark:bg-gray-700"
        onclick={handleProseClick}
      >
        {@html renderMarkdown(message.content)}
      </div>
    </div>

    <!-- ── Source cards ───────────────────────────────────────────── -->
    {#if message.sources && message.sources.length > 0}
      <div class="space-y-1.5">
        <p class="text-xs font-medium text-gray-400 dark:text-gray-500 px-1">Sources</p>
        <div class="grid grid-cols-1 gap-1.5">
          {#each message.sources as source, i}
            {@const idx = source.ref_id ?? i + 1}
            <div
              data-ref-id={idx}
              class="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden transition-shadow hover:shadow-sm"
            >
              <!-- Card header -->
              <button
                type="button"
                onclick={() => toggleSource(idx)}
                class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
              >
                <span class="shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 text-xs font-bold flex items-center justify-center">
                  {idx}
                </span>
                <span class="flex-1 text-xs font-medium text-gray-700 dark:text-gray-300 truncate">
                  {source.filename}
                </span>
                {#if source.section}
                  <span class="text-xs text-gray-400 dark:text-gray-500 shrink-0">{source.section}</span>
                {/if}
                <span class="shrink-0 px-1.5 py-0.5 rounded text-xs font-medium {scoreColor(source.score)}">
                  {(source.score * 100).toFixed(0)}%
                </span>
                <svg class="w-3.5 h-3.5 shrink-0 text-gray-400 transition-transform {expandedSources.has(idx) ? 'rotate-180' : ''}"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <!-- Card body (expandable) -->
              {#if expandedSources.has(idx)}
                <div class="px-3 pb-3 pt-2 border-t border-gray-100 dark:border-gray-700 space-y-1.5">
                  {#if source.content}
                    <p class="text-xs text-gray-600 dark:text-gray-400 leading-relaxed whitespace-pre-wrap">{source.content}</p>
                  {/if}
                  <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400 dark:text-gray-500 pt-1 border-t border-gray-100 dark:border-gray-700/50">
                    <span>Doc: <span class="font-mono text-gray-500 dark:text-gray-400 select-all">{source.document_id}</span></span>
                    {#if source.chunk_index !== undefined}
                      <span>Chunk #{source.chunk_index}</span>
                    {/if}
                  </div>
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- ── Action bar ─────────────────────────────────────────────── -->
    <div class="flex items-center gap-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
      <!-- Copy -->
      <button
        type="button"
        onclick={copyAnswer}
        title="Copy answer"
        class="flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
      >
        {#if copied}
          <svg class="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
          </svg>
          <span>Copied</span>
        {:else}
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
          <span>Copy</span>
        {/if}
      </button>

      <!-- Regenerate -->
      <button
        type="button"
        onclick={onRegenerate}
        title="Regenerate"
        class="flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        <span>Regenerate</span>
      </button>

      <div class="h-4 w-px bg-gray-200 dark:bg-gray-600 mx-1"></div>

      <!-- Thumbs up -->
      <button
        type="button"
        onclick={() => onFeedback(true)}
        title="Good response"
        class="p-1.5 rounded-md transition-colors
          {message.liked === true
            ? 'text-green-600 bg-green-50 dark:bg-green-900/30'
            : 'text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-600'}"
      >
        <svg class="w-3.5 h-3.5" fill={message.liked === true ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5" />
        </svg>
      </button>

      <!-- Thumbs down -->
      <button
        type="button"
        onclick={() => onFeedback(false)}
        title="Poor response"
        class="p-1.5 rounded-md transition-colors
          {message.liked === false
            ? 'text-red-600 bg-red-50 dark:bg-red-900/30'
            : 'text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-600'}"
      >
        <svg class="w-3.5 h-3.5" fill={message.liked === false ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018c.163 0 .326.02.485.06L17 4m-7 10v2a2 2 0 002 2h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5" />
        </svg>
      </button>
    </div>

  </div>
</div>
