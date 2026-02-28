<script lang="ts">
  import '../app.css';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import UserMenu from '$lib/components/UserMenu.svelte';
  import { goto } from '$app/navigation';
  import { isAuthEnabled, getAuthState, restoreAuthState, login, isPlatformAdmin } from '$lib/auth';
  import { Toaster } from 'svelte-sonner';

  let { children } = $props();
  let isReady = $state(false);
  let authState = $state(getAuthState());

  const publicRoutes = ['/auth/callback'];

  // $derived automatically recomputes whenever authState changes,
  // so nav updates the moment the role is known after login.
  let navItems = $derived.by(() => {
    const role = authState.user?.role ?? 'tenant_user';
    const items = [
      { href: '/', label: 'Chat' },
      { href: '/documents', label: 'Documents' },
    ];
    if (role === 'tenant_admin') items.push({ href: '/settings', label: 'Settings' });
    if (role === 'platform_admin') items.push({ href: '/admin', label: 'Admin' });
    return items;
  });
  
  function isActive(href: string): boolean {
    const currentPath = $page.url.pathname;
    if (href === '/') return currentPath === '/';
    return currentPath.startsWith(href);
  }
  
  function isPublicRoute(): boolean {
    return publicRoutes.some(route => $page.url.pathname.startsWith(route));
  }

  function guardRoute(path: string) {
    if (path.startsWith('/admin') && !isPlatformAdmin()) {
      goto('/');
    } else if (path.startsWith('/settings') && authState.user?.role !== 'tenant_admin' && authState.user?.role !== 'platform_admin') {
      goto('/');
    }
  }
  
  onMount(async () => {
    await restoreAuthState();
    authState = getAuthState(); // triggers $derived navItems recompute automatically
    
    if (isAuthEnabled() && !authState.isAuthenticated && !isPublicRoute()) {
      login();
      return;
    }

    guardRoute($page.url.pathname);
    isReady = true;
  });
</script>

<svelte:head>
  <title>DocIntel - Enterprise Document Intelligence</title>
  <meta name="description" content="AI-powered document Q&A with source citations" />
</svelte:head>

{#if !isReady && browser}
  <div class="flex items-center justify-center h-screen">
    <div class="text-center">
      <div class="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto mb-3 shadow-[0_0_12px_rgba(16,185,129,0.4)]"></div>
      <p class="text-sm text-slate-500">Connecting...</p>
    </div>
  </div>
{:else}
  <div class="flex flex-col h-screen">

    <!--
      Header: a transparent flow-spacer (h-14) that holds three absolutely
      positioned floating islands. The rest of the page renders naturally below.
    -->
    <header class="relative flex-shrink-0 h-14 z-50">

      <!-- ── Logo island (left) ── -->
      <div class="absolute left-4 top-1/2 -translate-y-1/2">
        <a href="/" class="flex items-center gap-2 px-3 py-1.5 glass-dark rounded-xl
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
