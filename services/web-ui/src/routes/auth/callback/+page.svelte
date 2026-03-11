<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { handleCallback } from '$lib/auth';

  let status = $state<'processing' | 'success' | 'error'>('processing');

  onMount(async () => {
    const result = await handleCallback();

    if (result?.isAuthenticated) {
      status = 'success';
      // Go directly to the app — skip the landing page to avoid an extra redirect hop.
      goto('/chat');
    } else {
      status = 'error';
      // Redirect back to landing page so the user can try again from a clean state.
      setTimeout(() => goto('/'), 1500);
    }
  });
</script>

<div class="min-h-screen flex items-center justify-center bg-gray-950">
  <div class="text-center space-y-4">
    {#if status === 'processing' || status === 'success'}
      <div class="w-10 h-10 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto
        shadow-[0_0_12px_rgba(16,185,129,0.4)]"></div>
      <p class="text-sm text-slate-400">
        {status === 'success' ? 'Signed in — redirecting…' : 'Completing sign-in…'}
      </p>
    {:else}
      <div class="w-12 h-12 rounded-full bg-red-500/10 ring-1 ring-red-500/30 flex items-center justify-center mx-auto">
        <svg class="w-5 h-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </div>
      <p class="text-sm text-slate-400">Authentication failed — returning to home…</p>
    {/if}
  </div>
</div>
