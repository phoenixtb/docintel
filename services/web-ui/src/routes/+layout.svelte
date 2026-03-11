<script lang="ts">
  import '../app.css';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import UserMenu from '$lib/components/UserMenu.svelte';
  import { goto, beforeNavigate } from '$app/navigation';
  import { getAuthState, login, authStore } from '$lib/auth';
  import { Toaster } from 'svelte-sonner';

  let { children } = $props();
  let isReady = $state(false);

  // authState is kept in sync with the authStore so the nav bar and guards
  // always reflect the current session — no hard refresh required.
  let authState = $state(getAuthState());
  authStore.subscribe(s => { authState = s; });

  const publicPaths = ['/auth/callback', '/'];

  function isPublicPath(path: string): boolean {
    // '/' is exact-match only (landing page); everything else is prefix-match
    if (path === '/') return true;
    return publicPaths.filter(p => p !== '/').some(p => path.startsWith(p));
  }

  function checkRoleGuard(path: string): boolean {
    const role = authState.user?.role ?? 'tenant_user';
    if (path.startsWith('/admin') && role !== 'platform_admin') return false;
    if (path.startsWith('/settings') && role === 'tenant_user') return false;
    return true;
  }

  // Fires on EVERY client-side navigation (not just initial mount).
  // Auth is always required — no toggle. Cancels and redirects if not authenticated
  // or lacks the required role for the target route.
  beforeNavigate(({ to, cancel }) => {
    if (!to) return;
    if (isPublicPath(to.url.pathname)) return;

    if (!authState.isAuthenticated) {
      cancel();
      login();
      return;
    }

    if (!checkRoleGuard(to.url.pathname)) {
      cancel();
      goto('/chat');
    }
  });

  let navItems = $derived.by(() => {
    const role = authState.user?.role ?? 'tenant_user';
    const items = [
      { href: '/chat', label: 'Chat' },
      { href: '/documents', label: 'Documents' },
    ];
    if (role === 'tenant_admin' || role === 'platform_admin') {
      items.push({ href: '/settings', label: 'Settings' });
    }
    if (role === 'platform_admin') {
      items.push({ href: '/admin', label: 'Admin' });
    }
    return items;
  });

  // True when on the landing page — the landing page renders its own minimal header
  let isLandingPage = $derived($page.url.pathname === '/');

  function isActive(href: string): boolean {
    const currentPath = $page.url.pathname;
    // Exact match for root-level routes; prefix match for nested (e.g. /admin/*)
    return currentPath === href || (href !== '/' && currentPath.startsWith(href + '/'));
  }

  onMount(() => {
    // Landing page is always ready; has its own header and public access.
    if ($page.url.pathname === '/') {
      isReady = true;
      return;
    }
    // hooks.client.ts init() has already restored the auth state before this
    // component mounts. We only need to check the initial route and show the app.
    // Auth is always required for protected routes.
    if (!authState.isAuthenticated && !isPublicPath($page.url.pathname)) {
      login();
      return;
    }
    if (!checkRoleGuard($page.url.pathname)) {
      goto('/chat');
      return;
    }
    isReady = true;
  });
</script>

<svelte:head>
  <title>DocIntel - Enterprise Document Intelligence</title>
  <meta name="description" content="AI-powered document Q&A with source citations" />
</svelte:head>

{#if !isReady && browser && !isLandingPage}
  <div class="flex items-center justify-center h-screen">
    <div class="text-center">
      <div class="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto mb-3 shadow-[0_0_12px_rgba(16,185,129,0.4)]"></div>
      <p class="text-sm text-slate-500">Connecting...</p>
    </div>
  </div>
{:else if isLandingPage}
  <!-- Landing page renders its own header; no shared chrome here -->
  {@render children()}
{:else}
  <div class="flex flex-col h-screen">

    <!--
      Header: hidden on the landing page (it has its own hero layout).
      On all other routes: transparent flow-spacer with three floating islands.
    -->
    <header class="relative flex-shrink-0 z-50
      {$page.url.pathname === '/' ? 'hidden' : 'h-14'}">

      <!-- ── Logo island (left) ── -->
      <div class="absolute left-4 top-1/2 -translate-y-1/2">
        <a href="/chat" class="flex items-center gap-2 px-3 py-1.5 glass-dark rounded-xl
          hover:opacity-80 transition-opacity shadow-glass-sm">
          <img src="/logos/docintel_logo.png" alt="DocIntel" class="h-6 w-auto" />
          <span class="text-sm font-semibold text-white tracking-wide">DocIntel</span>
        </a>
      </div>

      <!-- ── Nav island (center) ── -->
      <div class="absolute left-1/2 -translate-x-1/2 top-1/2 -translate-y-1/2">
        <nav class="flex items-center gap-0.5 glass-dark rounded-full px-1.5 py-1.5 shadow-glass-sm">
          {#each navItems as item}
            <a
              href={item.href}
              class="px-4 py-1 rounded-full text-xs font-medium transition-all duration-200
                {isActive(item.href)
                  ? 'bg-emerald-500/15 text-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                  : 'text-slate-400 hover:text-emerald-300 hover:bg-white/5'}"
            >
              {item.label}
            </a>
          {/each}
        </nav>
      </div>

      <!-- ── User menu (right) ── -->
      <div class="absolute right-4 top-1/2 -translate-y-1/2">
        <UserMenu />
      </div>

    </header>

    <!-- Main content — starts naturally below the header spacer -->
    <main class="flex-1 overflow-auto min-h-0">
      {@render children()}
    </main>
  </div>
{/if}

<Toaster theme="dark" position="bottom-right" richColors />
