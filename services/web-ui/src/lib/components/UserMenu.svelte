<script lang="ts">
  import { onMount } from 'svelte';
  import { 
    isAuthEnabled, 
    getAuthState, 
    login, 
    logout 
  } from '$lib/auth';
  
  type Theme = 'light' | 'dark' | 'system';
  
  let isOpen = $state(false);
  let currentTheme: Theme = $state('system');
  let authState = $state(getAuthState());
  
  // Re-read auth state periodically to pick up changes from layout's restoreAuthState
  $effect(() => {
    const interval = setInterval(() => {
      const current = getAuthState();
      if (current.isAuthenticated !== authState.isAuthenticated) {
        authState = current;
      }
    }, 500);
    return () => clearInterval(interval);
  });
  
  const themes: { value: Theme; label: string; icon: string }[] = [
    { value: 'light', label: 'Light', icon: '☀️' },
    { value: 'dark', label: 'Dark', icon: '🌙' },
    { value: 'system', label: 'System', icon: '💻' },
  ];
  
  function getSystemTheme(): 'light' | 'dark' {
    if (typeof window === 'undefined') return 'light';
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  
  function applyTheme(theme: Theme) {
    const effectiveTheme = theme === 'system' ? getSystemTheme() : theme;
    if (effectiveTheme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }
  
  function setTheme(theme: Theme) {
    currentTheme = theme;
    localStorage.setItem('theme', theme);
    applyTheme(theme);
  }
  
  function handleLogin() {
    isOpen = false;
    login();
  }
  
  function handleLogout() {
    isOpen = false;
    logout();
    authState = getAuthState();
  }
  
  onMount(async () => {
    // Read latest auth state (layout already restored it)
    authState = getAuthState();
    
    // Restore theme
    const saved = localStorage.getItem('theme') as Theme | null;
    if (saved && ['light', 'dark', 'system'].includes(saved)) {
      currentTheme = saved;
    }
    applyTheme(currentTheme);
    
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      if (currentTheme === 'system') applyTheme('system');
    };
    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  });
</script>

<div class="relative">
  <button
    onclick={() => isOpen = !isOpen}
    class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
  >
    <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white font-medium text-sm">
      {authState.user?.name?.charAt(0).toUpperCase() || 'U'}
    </div>
    <svg class="w-4 h-4 text-gray-500 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
    </svg>
  </button>
  
  {#if isOpen}
    <!-- Backdrop -->
    <button 
      class="fixed inset-0 z-40" 
      onclick={() => isOpen = false}
      aria-label="Close menu"
    ></button>
    
    <!-- Dropdown -->
    <div class="absolute right-0 mt-2 w-64 py-2 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 z-50">
      <!-- User Info -->
      <div class="px-4 py-3 border-b border-gray-100 dark:border-gray-700">
        {#if authState.isAuthenticated && authState.user}
          <p class="text-sm font-medium text-gray-900 dark:text-white">{authState.user.name}</p>
          <p class="text-xs text-gray-500 dark:text-gray-400">{authState.user.email}</p>
          <p class="text-xs text-blue-600 dark:text-blue-400 mt-1">Tenant: {authState.user.tenantId}</p>
        {:else}
          <p class="text-sm font-medium text-gray-900 dark:text-white">Guest</p>
          <p class="text-xs text-gray-500 dark:text-gray-400">Not signed in</p>
        {/if}
      </div>
      
      <!-- Theme Selection -->
      <div class="px-4 py-3 border-b border-gray-100 dark:border-gray-700">
        <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Theme</p>
        <div class="flex gap-1">
          {#each themes as theme}
            <button
              onclick={() => setTheme(theme.value)}
              class="flex-1 px-3 py-1.5 text-sm rounded-lg transition-colors flex items-center justify-center gap-1
                {currentTheme === theme.value 
                  ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' 
                  : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-400'}"
              title={theme.label}
            >
              <span>{theme.icon}</span>
              <span class="text-xs">{theme.label}</span>
            </button>
          {/each}
        </div>
      </div>
      
      <!-- Auth Actions -->
      <div class="pt-2">
        {#if isAuthEnabled()}
          {#if authState.isAuthenticated}
            <button 
              onclick={handleLogout}
              class="w-full px-4 py-2 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
              Sign out
            </button>
          {:else}
            <button 
              onclick={handleLogin}
              class="w-full px-4 py-2 text-left text-sm text-blue-600 dark:text-blue-400 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 font-medium"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1" />
              </svg>
              Sign in
            </button>
          {/if}
        {:else}
          <div class="px-4 py-2 text-xs text-gray-400 dark:text-gray-500">
            Auth disabled (dev mode)
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>
