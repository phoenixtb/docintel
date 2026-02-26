<script lang="ts">
  import '../app.css';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import UserMenu from '$lib/components/UserMenu.svelte';
  import { isAuthEnabled, getAuthState, restoreAuthState, login } from '$lib/auth';
  import { Toaster } from 'svelte-sonner';

  let { children } = $props();
  let isReady = $state(false);
  let authState = $state(getAuthState());
  
  const navItems = [
    { href: '/', label: 'Chat', icon: 'chat' },
    { href: '/documents', label: 'Documents', icon: 'docs' },
    { href: '/admin', label: 'Admin', icon: 'admin' },
  ];
  
  // Public routes that don't require auth
  const publicRoutes = ['/auth/callback'];
  
  function isActive(href: string): boolean {
    const currentPath = $page.url.pathname;
    if (href === '/') return currentPath === '/';
    return currentPath.startsWith(href);
  }
  
  function isPublicRoute(): boolean {
    return publicRoutes.some(route => $page.url.pathname.startsWith(route));
  }
  
  onMount(async () => {
    // Restore auth state from oidc-client-ts session store
    await restoreAuthState();
    authState = getAuthState();
    
    // Check if we need to redirect to login
    if (isAuthEnabled() && !authState.isAuthenticated && !isPublicRoute()) {
      login();
      return;
    }
    
    isReady = true;
  });
</script>

<svelte:head>
  <title>DocIntel - Enterprise Document Intelligence</title>
  <meta name="description" content="AI-powered document Q&A with source citations" />
</svelte:head>

{#if !isReady && browser}
  <!-- Brief loading state while checking auth -->
  <div class="flex items-center justify-center h-screen bg-gray-50 dark:bg-gray-900">
    <div class="text-center">
      <div class="w-8 h-8 border-3 border-blue-600 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
      <p class="text-sm text-gray-500 dark:text-gray-400">Connecting...</p>
    </div>
  </div>
{:else}
  <div class="flex flex-col h-screen bg-gray-50 dark:bg-gray-900">
    <!-- Header -->
    <header class="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-3">
      <div class="flex items-center justify-between">
        <!-- Logo + Nav -->
        <div class="flex items-center gap-8">
          <!-- Logo -->
          <a href="/" class="flex items-center gap-3 hover:opacity-80">
            <img 
              src="/logos/docintel_logo.png" 
              alt="DocIntel" 
              class="h-8 w-auto"
            />
            <span class="text-xl font-semibold text-gray-900 dark:text-white">DocIntel</span>
          </a>
          
          <!-- Navigation -->
          <nav class="flex items-center gap-1">
            {#each navItems as item}
              <a
                href={item.href}
                class="px-4 py-2 rounded-lg text-sm font-medium transition-colors
                  {isActive(item.href)
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-900 dark:hover:text-white'}"
              >
                {#if item.icon === 'chat'}
                  <span class="inline-flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                    </svg>
                    {item.label}
                  </span>
                {:else if item.icon === 'docs'}
                  <span class="inline-flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    {item.label}
                  </span>
                {:else if item.icon === 'admin'}
                  <span class="inline-flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                    {item.label}
                  </span>
                {:else}
                  {item.label}
                {/if}
              </a>
            {/each}
          </nav>
        </div>
        
        <!-- User Menu -->
        <UserMenu />
      </div>
    </header>
    
    <!-- Main Content -->
    <main class="flex-1 overflow-auto min-h-0">
      {@render children()}
    </main>
  </div>
{/if}

<Toaster theme="dark" position="bottom-right" richColors />
