<script lang="ts">
  let {
    summary,
    compressedTurns,
  }: {
    summary: string;
    compressedTurns: number;
  } = $props();

  let expanded = $state(false);
</script>

<div class="relative flex items-center gap-3 py-2 select-none">
  <!-- Left rule -->
  <div class="flex-1 h-px bg-gradient-to-r from-transparent to-slate-700/60"></div>

  <!-- Pill button -->
  <button
    onclick={() => expanded = !expanded}
    class="flex items-center gap-1.5 px-3 py-1 rounded-full
      border border-slate-700/60 bg-slate-800/60 backdrop-blur-sm
      text-xs text-slate-500 hover:text-slate-300 hover:border-slate-600
      transition-all duration-200 group shrink-0"
    title={expanded ? 'Collapse earlier context' : 'View earlier context summary'}
  >
    <svg class="w-3 h-3 shrink-0 text-indigo-400/70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M4 6h16M4 10h16M4 14h8" />
    </svg>
    <span>
      {compressedTurns} earlier {compressedTurns === 1 ? 'turn' : 'turns'} summarized
    </span>
    <svg
      class="w-3 h-3 shrink-0 transition-transform duration-200 {expanded ? 'rotate-180' : ''}"
      fill="none" stroke="currentColor" viewBox="0 0 24 24"
    >
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
    </svg>
  </button>

  <!-- Right rule -->
  <div class="flex-1 h-px bg-gradient-to-l from-transparent to-slate-700/60"></div>
</div>

{#if expanded}
  <div class="mx-1 mb-3 rounded-xl border border-slate-700/50 bg-slate-800/40 backdrop-blur-sm overflow-hidden animate-fade-in">
    <div class="flex items-center gap-2 px-3 py-2 border-b border-slate-700/40">
      <svg class="w-3.5 h-3.5 text-indigo-400/70 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      <span class="text-xs font-medium text-slate-400">What the AI remembers from earlier</span>
    </div>
    <p class="px-3 py-2.5 text-xs text-slate-500 leading-relaxed whitespace-pre-wrap">{summary}</p>
  </div>
{/if}
