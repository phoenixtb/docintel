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
    domain?: string;
  }

  interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    thinking?: string;
    sources?: Source[];
    liked?: boolean | null;
    queryId?: string;
    routedDomain?: string;
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
      card?.classList.add('ring-1', 'ring-emerald-400');
      setTimeout(() => card?.classList.remove('ring-1', 'ring-emerald-400'), 1200);
    });
  }

  function scoreColor(score: number): string {
    if (score >= 0.8) return 'bg-emerald-900/50 text-emerald-300';
    if (score >= 0.5) return 'bg-amber-900/50 text-amber-300';
    return 'bg-white/5 text-slate-400';
  }

  const DOMAIN_CHIP: Record<string, string> = {
    hr_policy: 'bg-purple-900/50 text-purple-300',
    technical:  'bg-cyan-900/50   text-cyan-300',
    contracts:  'bg-amber-900/50  text-amber-300',
    general:    'bg-white/5       text-slate-400',
  };

  const DOMAIN_LABEL: Record<string, string> = {
    hr_policy: 'HR',
    technical:  'Tech',
    contracts:  'Contract',
    general:    'General',
  };

  function domainChip(domain: string): { cls: string; label: string } {
    return {
      cls:   DOMAIN_CHIP[domain]  ?? DOMAIN_CHIP['general'],
      label: DOMAIN_LABEL[domain] ?? domain,
    };
  }
</script>

<style>
  /* ── Shared prose-ai layout ── */
  :global(.prose-ai p)   { margin: 0.375rem 0; }
  :global(.prose-ai ul)  { list-style: disc; padding-left: 1.25rem; margin: 0.375rem 0; }
  :global(.prose-ai ol)  { list-style: decimal; padding-left: 1.25rem; margin: 0.375rem 0; }
  :global(.prose-ai li)  { margin: 0.2rem 0; }
  :global(.prose-ai h1, .prose-ai h2, .prose-ai h3, .prose-ai h4) {
    font-weight: 600;
    margin: 0.75rem 0 0.25rem;
  }
  :global(.prose-ai h1) { font-size: 1.15rem; }
  :global(.prose-ai h2) { font-size: 1.05rem; }
  :global(.prose-ai h3) { font-size: 0.95rem; }
  :global(.prose-ai blockquote) {
    border-left: 2px solid rgba(16,185,129,0.4);
    padding-left: 0.75rem;
    font-style: italic;
  }
  :global(.prose-ai table) { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  :global(.prose-ai th) { background: rgba(16,185,129,0.08); padding: 0.4rem 0.6rem; }
  :global(.prose-ai td) { padding: 0.35rem 0.6rem; }
  :global(.prose-ai hr) { margin: 0.75rem 0; }

  /* ── Dark mode colors ── */
  :global(html.dark .prose-ai)          { color: #cbd5e1; }
  :global(html.dark .prose-ai h1, html.dark .prose-ai h2,
          html.dark .prose-ai h3, html.dark .prose-ai h4) { color: #e2e8f0; }
  :global(html.dark .prose-ai strong)   { color: #e2e8f0; font-weight: 600; }
  :global(html.dark .prose-ai a)        { color: #34d399; text-decoration: underline; }
  :global(html.dark .prose-ai blockquote) { color: #94a3b8; }
  :global(html.dark .prose-ai th)       { color: #6ee7b7; border: 1px solid rgba(255,255,255,0.06); }
  :global(html.dark .prose-ai td)       { border: 1px solid rgba(255,255,255,0.06); color: #94a3b8; }
  :global(html.dark .prose-ai hr)       { border-color: rgba(255,255,255,0.06); }

  /* ── Light mode colors ── */
  :global(html:not(.dark) .prose-ai)         { color: #334155; }
  :global(html:not(.dark) .prose-ai h1, html:not(.dark) .prose-ai h2,
          html:not(.dark) .prose-ai h3, html:not(.dark) .prose-ai h4) { color: #0f172a; }
  :global(html:not(.dark) .prose-ai strong)  { color: #0f172a; font-weight: 600; }
  :global(html:not(.dark) .prose-ai a)       { color: #059669; text-decoration: underline; }
  :global(html:not(.dark) .prose-ai blockquote) { color: #64748b; }
  :global(html:not(.dark) .prose-ai th)      { color: #065f46; border: 1px solid rgba(0,0,0,0.08); }
  :global(html:not(.dark) .prose-ai td)      { border: 1px solid rgba(0,0,0,0.08); color: #475569; }
  :global(html:not(.dark) .prose-ai hr)      { border-color: rgba(0,0,0,0.08); }
</style>

<div class="group flex justify-start animate-fade-in">
  <div class="max-w-3xl w-full space-y-2">

    <!-- ── Thinking section (collapsible) ─────────────────────────── -->
    {#if message.thinking}
      <details class="rounded-xl border border-dashed border-emerald-500/20 bg-emerald-950/15 backdrop-blur-sm text-xs">
        <summary class="flex items-center gap-2 px-3 py-2 cursor-pointer select-none text-emerald-600 hover:text-emerald-400 list-none transition-colors">
          <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          <span class="font-medium">Thinking</span>
          <span class="ml-auto text-slate-600">(click to expand)</span>
        </summary>
        <pre class="px-3 pb-3 pt-1 font-mono text-xs text-slate-500 whitespace-pre-wrap overflow-x-auto leading-relaxed">{message.thinking}</pre>
      </details>
    {/if}

    <!-- ── Routing indicator ──────────────────────────────────────── -->
    {#if message.routedDomain}
      {@const chip = domainChip(message.routedDomain)}
      <div class="flex items-center gap-1.5 px-1 text-xs text-slate-600">
        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
        </svg>
        <span>Searched in</span>
        <span class="px-1.5 py-0.5 rounded-full font-medium {chip.cls}">{chip.label}</span>
        <span class="text-slate-700">(auto-routed)</span>
      </div>
    {/if}

    <!-- ── Answer bubble ──────────────────────────────────────────── -->
    <div class="px-4 py-4 rounded-2xl glass border border-emerald-500/10 hover:border-emerald-500/15 transition-all duration-200
      dark:shadow-none shadow-sm">
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div
        role="presentation"
        class="prose-ai text-sm leading-relaxed max-w-none"
        onclick={handleProseClick}
      >
        {@html renderMarkdown(message.content)}
      </div>
    </div>

    <!-- ── Source cards ───────────────────────────────────────────── -->
    {#if message.sources && message.sources.length > 0}
      <div class="space-y-1.5">
        <p class="text-xs font-medium text-slate-600 px-1 flex items-center gap-1.5">
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Sources
        </p>
        <div class="grid grid-cols-1 gap-1.5">
          {#each message.sources as source, i}
            {@const idx = source.ref_id ?? i + 1}
            <div
              data-ref-id={idx}
              class="rounded-xl glass border border-white/5 hover:border-emerald-500/20 overflow-hidden transition-all duration-200"
            >
              <!-- Card header -->
              <button
                type="button"
                onclick={() => toggleSource(idx)}
                class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-emerald-500/5 transition-colors"
              >
                <span class="shrink-0 w-5 h-5 rounded-full
                  bg-emerald-900/60 text-emerald-400
                  text-xs font-bold flex items-center justify-center
                  border border-emerald-500/30">
                  {idx}
                </span>
                <span class="flex-1 text-xs font-medium text-slate-300 truncate">
                  {source.filename}
                </span>
                {#if source.domain}
                  {@const chip = domainChip(source.domain)}
                  <span class="shrink-0 px-1.5 py-0.5 rounded-full text-xs font-medium {chip.cls}">
                    {chip.label}
                  </span>
                {/if}
                {#if source.section}
                  <span class="text-xs text-slate-600 shrink-0">{source.section}</span>
                {/if}
                <span class="shrink-0 px-1.5 py-0.5 rounded text-xs font-medium {scoreColor(source.score)}">
                  {(source.score * 100).toFixed(0)}%
                </span>
                <svg class="w-3 h-3 shrink-0 text-slate-600 transition-transform {expandedSources.has(idx) ? 'rotate-180' : ''}"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <!-- Card body (expandable) -->
              {#if expandedSources.has(idx)}
                <div class="px-3 pb-3 pt-2 border-t border-white/5 space-y-1.5">
                  {#if source.content}
                    <p class="text-xs text-slate-500 leading-relaxed whitespace-pre-wrap">{source.content}</p>
                  {/if}
                  <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 pt-1 border-t border-white/5">
                    <span>Doc: <span class="font-mono text-slate-500 select-all">{source.document_id}</span></span>
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
    <div class="flex items-center gap-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
      <!-- Copy -->
      <button
        type="button"
        onclick={copyAnswer}
        title="Copy answer"
        class="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
      >
        {#if copied}
          <svg class="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
          </svg>
          <span class="text-emerald-400">Copied</span>
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
        class="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        <span>Regenerate</span>
      </button>

      <div class="h-3 w-px bg-white/10 mx-1"></div>

      <!-- Thumbs up -->
      <button
        type="button"
        onclick={() => onFeedback(true)}
        title="Good response"
        class="p-1.5 rounded-lg transition-colors
          {message.liked === true
            ? 'text-emerald-400 bg-emerald-500/10'
            : 'text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10'}"
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
        class="p-1.5 rounded-lg transition-colors
          {message.liked === false
            ? 'text-red-400 bg-red-500/10'
            : 'text-slate-500 hover:text-red-400 hover:bg-red-500/10'}"
      >
        <svg class="w-3.5 h-3.5" fill={message.liked === false ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018c.163 0 .326.02.485.06L17 4m-7 10v2a2 2 0 002 2h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5" />
        </svg>
      </button>
    </div>

  </div>
</div>
