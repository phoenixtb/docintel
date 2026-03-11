<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { login, authStore, getAuthState } from '$lib/auth';

  let authState = $state(getAuthState());
  authStore.subscribe(s => { authState = s; });

  function handleGetStarted() {
    if (authState.isAuthenticated) {
      goto('/chat');
    } else {
      login();
    }
  }

  // Redirect immediately if already authenticated
  $effect(() => {
    if (authState.isAuthenticated) goto('/chat');
  });

  const features = [
    {
      icon: `<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />`,
      color: 'emerald',
      title: 'Conversational Q&A',
      desc: 'Ask anything in plain English. Get precise answers drawn directly from your documents, with numbered inline citations linking back to the exact source chunk.',
      tag: 'Ollama · Qwen3.5',
    },
    {
      icon: `<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />`,
      color: 'indigo',
      title: 'Hybrid Search',
      desc: 'Dense vector similarity combined with BM25 sparse retrieval, re-ranked by a cross-encoder. Best-of-both-worlds precision that neither approach achieves alone.',
      tag: 'Qdrant · Haystack',
    },
    {
      icon: `<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />`,
      color: 'violet',
      title: 'Domain Routing',
      desc: 'Queries are automatically classified and routed to the right knowledge domain — HR policies, technical docs, or legal contracts — before retrieval begins.',
      tag: 'Auto-classified',
    },
    {
      icon: `<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />`,
      color: 'indigo',
      title: 'Multi-Tenant Security',
      desc: 'Row-level SQL security and per-tenant Qdrant namespaces ensure teams are fully isolated. OIDC via Authentik with role-based access for admins and users.',
      tag: 'Authentik · PostgreSQL',
    },
    {
      icon: `<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M13 10V3L4 14h7v7l9-11h-7z" />`,
      color: 'cyan',
      title: 'Semantic Caching',
      desc: 'Near-duplicate questions hit a vector-similarity cache on Redis before touching the LLM, cutting latency from seconds to milliseconds on repeated queries.',
      tag: 'Redis · Vector similarity',
    },
    {
      icon: `<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />`,
      color: 'amber',
      title: 'Universal Ingest',
      desc: 'Upload PDFs, plain text, legal contracts, or HR policies. Documents are chunked, embedded with nomic-embed-text, and instantly searchable via the hybrid pipeline.',
      tag: 'MinIO · nomic-embed',
    },
  ];

  const colorMap: Record<string, { bg: string; border: string; hover: string; icon: string; tag: string }> = {
    emerald: {
      bg: 'bg-emerald-500/10', border: 'border-emerald-500/20',
      hover: 'hover:border-emerald-500/40', icon: 'text-emerald-400',
      tag: 'bg-emerald-500/10 text-emerald-500/80',
    },
    indigo: {
      bg: 'bg-indigo-500/10', border: 'border-indigo-500/20',
      hover: 'hover:border-indigo-500/40', icon: 'text-indigo-400',
      tag: 'bg-indigo-500/10 text-indigo-400/80',
    },
    violet: {
      bg: 'bg-violet-500/10', border: 'border-violet-500/20',
      hover: 'hover:border-violet-500/40', icon: 'text-violet-400',
      tag: 'bg-violet-500/10 text-violet-400/80',
    },
    cyan: {
      bg: 'bg-cyan-500/10', border: 'border-cyan-500/20',
      hover: 'hover:border-cyan-500/40', icon: 'text-cyan-400',
      tag: 'bg-cyan-500/10 text-cyan-400/80',
    },
    amber: {
      bg: 'bg-amber-500/10', border: 'border-amber-500/20',
      hover: 'hover:border-amber-500/40', icon: 'text-amber-400',
      tag: 'bg-amber-500/10 text-amber-400/80',
    },
  };
</script>

<svelte:head>
  <title>DocIntel - Enterprise Document Intelligence</title>
  <meta name="description" content="Ask anything about your team's private knowledge. DocIntel reads your documents and gives cited, accurate answers — instantly." />
</svelte:head>

<style>
  /* Gradient headline text with AI glow */
  .hero-glow {
    filter: drop-shadow(0 0 28px rgba(16, 185, 129, 0.5));
  }
  .hero-gradient {
    background: linear-gradient(135deg, #34d399 0%, #10b981 30%, #818cf8 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
</style>

<!-- Full-viewport landing, layout header is hidden on this route -->
<div class="min-h-screen flex flex-col dark:text-slate-100 text-slate-800">

  <!-- ══════════════════════════════════════════════════════════════════
       Two-column layout: stacks vertically on mobile (left on top)
       ══════════════════════════════════════════════════════════════════ -->
  <div class="flex-1 flex flex-col lg:flex-row">

    <!-- ── LEFT COLUMN: Hero ─────────────────────────────────────────── -->
    <div class="flex flex-col justify-center px-8 sm:px-12 lg:px-14 xl:px-16
      py-14 lg:py-0 lg:w-[46%] xl:w-[44%] flex-shrink-0">

      <!-- Logo -->
      <div class="mb-10">
        <img src="/logos/docintel_logo.png" alt="DocIntel" class="w-[60%] max-w-[240px] h-auto" />
      </div>

      <!-- Badge -->
      <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium mb-7
        bg-emerald-500/10 border border-emerald-500/25 text-emerald-600 dark:text-emerald-400 self-start">
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_6px_rgba(16,185,129,0.8)]"></span>
        Enterprise RAG · Multi-Tenant · Local-first
      </div>

      <!-- Headline -->
      <h1 class="text-4xl sm:text-5xl xl:text-[3.5rem] font-bold leading-tight tracking-tight mb-5 dark:text-white text-slate-900">
        Your documents,<br />
        <span class="hero-glow">
          <span class="hero-gradient">answered.</span>
        </span>
      </h1>

      <!-- Sub-headline -->
      <p class="text-base dark:text-slate-400 text-slate-500 mb-9 leading-relaxed max-w-sm">
        Ask anything about your team's private knowledge. DocIntel reads your documents
        and gives cited, accurate answers with full source traceability.
      </p>

      <!-- ── Three wide action buttons ── -->
      <div class="flex flex-col gap-3 max-w-[320px]">

        <!-- Primary: Sign In -->
        <button
          onclick={handleGetStarted}
          class="group w-full flex items-center justify-between px-5 py-3.5 rounded-xl
            bg-emerald-500 hover:bg-emerald-400 text-white font-semibold text-sm
            shadow-[0_0_24px_rgba(16,185,129,0.4)] hover:shadow-[0_0_40px_rgba(16,185,129,0.65)]
            transition-all duration-200"
        >
          <span>Sign In</span>
          <svg class="w-4 h-4 transition-transform group-hover:translate-x-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
          </svg>
        </button>

        <!-- Secondary: See Features -->
        <a
          href="https://titasbiswas.com/work/docintel"
          target="_blank"
          rel="noopener noreferrer"
          class="group w-full flex items-center justify-between px-5 py-3.5 rounded-xl
            glass-dark border border-emerald-500/25 hover:border-emerald-500/50
            dark:text-slate-300 text-slate-600 hover:text-emerald-400 font-semibold text-sm
            transition-all duration-200"
        >
          <span>See Features</span>
          <svg class="w-3.5 h-3.5 opacity-50 group-hover:opacity-100 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>

        <!-- Tertiary: GitHub -->
        <a
          href="https://github.com/phoenixtb/docintel"
          target="_blank"
          rel="noopener noreferrer"
          class="group w-full flex items-center justify-between px-5 py-3.5 rounded-xl
            border dark:border-white/5 border-slate-200/60 hover:dark:border-white/10 hover:border-slate-300/80
            dark:text-slate-500 text-slate-400 hover:dark:text-slate-300 hover:text-slate-600
            font-medium text-sm transition-all duration-200"
        >
          <span class="flex items-center gap-2">
            <!-- GitHub mark -->
            <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
            </svg>
            GitHub
          </span>
          <svg class="w-3.5 h-3.5 opacity-40 group-hover:opacity-70 transition-opacity" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      </div>

    </div>

    <!-- ── Vertical divider (large screens only) ── -->
    <div class="hidden lg:block flex-shrink-0 w-px my-10"
      style="background: linear-gradient(to bottom, transparent, rgba(255,255,255,0.08) 30%, rgba(255,255,255,0.08) 70%, transparent)">
    </div>

    <!-- ── RIGHT COLUMN: Features ─────────────────────────────────────── -->
    <div class="flex-1 flex flex-col justify-center px-8 sm:px-12 lg:px-14 xl:px-16
      pb-14 pt-4 lg:py-12">

      <p class="text-[10px] font-bold dark:text-slate-600 text-slate-400 uppercase tracking-[0.15em] mb-5">
        Built on production RAG patterns
      </p>

      <div class="grid sm:grid-cols-2 gap-3">
        {#each features as f}
          {@const c = colorMap[f.color]}
          <div class="glass-dark rounded-2xl p-5 border {c.border} {c.hover} transition-all duration-200
            hover:shadow-[0_0_20px_rgba(0,0,0,0.2)] group">

            <!-- Icon -->
            <div class="w-9 h-9 rounded-xl flex items-center justify-center mb-4 flex-shrink-0
              {c.bg} border {c.border}">
              <svg class="w-4.5 h-4.5 {c.icon}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {@html f.icon}
              </svg>
            </div>

            <!-- Title -->
            <h3 class="text-sm font-semibold dark:text-white text-slate-800 mb-1.5">{f.title}</h3>

            <!-- Description -->
            <p class="text-xs dark:text-slate-500 text-slate-500 leading-relaxed mb-3">{f.desc}</p>

            <!-- Tech tag -->
            <span class="inline-block text-[10px] font-medium px-2 py-0.5 rounded-full {c.tag}">
              {f.tag}
            </span>
          </div>
        {/each}
      </div>
    </div>

  </div>

  <!-- ── Footer ── -->
  <footer class="flex-shrink-0 border-t dark:border-white/5 border-slate-200/60 py-4
    text-center text-xs dark:text-slate-700 text-slate-400">
    DocIntel &copy; {new Date().getFullYear()} &mdash; Enterprise Document Intelligence
  </footer>

</div>
