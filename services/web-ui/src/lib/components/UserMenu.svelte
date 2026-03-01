<script lang="ts">
  import { onMount } from 'svelte';
  import { 
    isAuthEnabled, 
    getAuthState, 
    login, 
    logout,
    getRole,
    isPlatformAdmin,
    isTenantAdmin
  } from '$lib/auth';
  
  type Theme = 'light' | 'dark';
  
  let isOpen = $state(false);
  let currentTheme: Theme = $state('dark');
  let authState = $state(getAuthState());
  
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
    { value: 'dark',  label: 'Dark',  icon: '🌙' },
    { value: 'light', label: 'Light', icon: '☀️' },
  ];
  
  function applyTheme(theme: Theme) {
    if (theme === 'dark') {
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
  
  onMount(() => {
    authState = getAuthState();
    
    // Only 'light' explicitly switches away from dark. Everything else → dark.
    const saved = localStorage.getItem('theme');
    currentTheme = saved === 'light' ? 'light' : 'dark';
    applyTheme(currentTheme);
  });
</script>

<div class="relative">
  <button
    onclick={() => isOpen = !isOpen}
    class="flex items-center gap-2 px-2.5 py-1.5 rounded-xl hover:bg-white/5 transition-colors group"
  >
    <div class="w-7 h-7 bg-emerald-600 rounded-full flex items-center justify-center text-white font-semibold text-xs
      shadow-[0_0_10px_rgba(16,185,129,0.3)] group-hover:shadow-[0_0_14px_rgba(16,185,129,0.5)] transition-shadow">
      {authState.user?.name?.charAt(0).toUpperCase() || 'U'}
    </div>
    <svg class="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
    <div class="absolute right-0 mt-2 w-64 py-2 rounded-2xl z-50
      glass-dark border border-white/5 shadow-[0_20px_60px_rgba(0,0,0,0.6)]">

      <!-- User Info -->
      <div class="px-4 py-3 border-b border-white/5">
        {#if authState.isAuthenticated && authState.user}
          <p class="text-sm font-semibold text-slate-200">{authState.user.name}</p>
          <p class="text-xs text-slate-500 mt-0.5">{authState.user.email}</p>
          <div class="mt-2 flex items-center gap-2">
            {#if isPlatformAdmin()}
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium
                bg-violet-500/15 text-violet-400 ring-1 ring-violet-500/25">
                <svg class="w-2.5 h-2.5" fill="currentColor" viewBox="0 0 20 20">
                  <path fill-rule="evenodd" d="M9.664 1.319a.75.75 0 01.672 0 41.059 41.059 0 018.198 5.424.75.75 0 01-.254 1.285 31.372 31.372 0 00-7.86 3.83.75.75 0 01-.84 0 31.508 31.508 0 00-2.08-1.287V9.48a.75.75 0 01.327-.64A33.14 33.14 0 019.664 7.16V1.319zm0 0" clip-rule="evenodd"/>
                </svg>
                Platform Admin
              </span>
            {:else if isTenantAdmin()}
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium
                bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/25">
                Admin
              </span>
              <span class="text-xs text-slate-500">
                {authState.user.tenantId.charAt(0).toUpperCase() + authState.user.tenantId.slice(1)}
              </span>
            {:else}
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium
                bg-emerald-500/10 text-emerald-500/80 ring-1 ring-emerald-500/20">
                {authState.user.tenantId.charAt(0).toUpperCase() + authState.user.tenantId.slice(1)}
              </span>
            {/if}
          </div>
        {:else}
          <p class="text-sm font-semibold text-slate-200">Guest</p>
          <p class="text-xs text-slate-500">Not signed in</p>
        {/if}
      </div>
      
      <!-- Theme Selection -->
      <div class="px-4 py-3 border-b border-white/5">
        <p class="text-xs font-medium text-slate-600 uppercase tracking-wider mb-2">Theme</p>
        <div class="flex gap-1">
          {#each themes as theme}
            <button
              onclick={() => setTheme(theme.value)}
              class="flex-1 px-2 py-1.5 text-xs rounded-lg transition-all flex items-center justify-center gap-1
                {currentTheme === theme.value 
                  ? 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30' 
                  : 'hover:bg-white/5 text-slate-500 hover:text-slate-300'}"
              title={theme.label}
            >
              <span>{theme.icon}</span>
              <span>{theme.label}</span>
            </button>
          {/each}
        </div>
      </div>
      
      <!-- Auth Actions -->
      <div class="pt-1">
        {#if isAuthEnabled()}
          {#if authState.isAuthenticated}
            <button 
              onclick={handleLogout}
              class="w-full px-4 py-2.5 text-left text-sm text-slate-400 hover:text-slate-200 hover:bg-white/5 flex items-center gap-2 transition-colors"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
              Sign out
            </button>
          {:else}
            <button 
              onclick={handleLogin}
              class="w-full px-4 py-2.5 text-left text-sm text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/10 flex items-center gap-2 font-medium transition-colors"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1" />
              </svg>
              Sign in
            </button>
          {/if}
        {:else}
          <div class="px-4 py-2 text-xs text-slate-600">
            Auth disabled (dev mode)
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>
