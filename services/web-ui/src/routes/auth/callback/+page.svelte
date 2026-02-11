<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { handleCallback } from '$lib/auth';
  
  let status = $state<'processing' | 'success' | 'error'>('processing');
  let errorMessage = $state('');
  
  onMount(async () => {
    // oidc-client-ts reads code/state from the URL automatically
    const result = await handleCallback();
    
    if (result?.isAuthenticated) {
      status = 'success';
      setTimeout(() => goto('/'), 500);
    } else {
      status = 'error';
      errorMessage = 'Failed to complete authentication';
    }
  });
</script>

<div class="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
  <div class="max-w-md w-full p-8 bg-white dark:bg-gray-800 rounded-xl shadow-lg text-center">
    {#if status === 'processing'}
      <div class="w-16 h-16 border-4 border-blue-600 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
      <h2 class="text-xl font-semibold text-gray-900 dark:text-white mb-2">Signing you in...</h2>
      <p class="text-gray-500 dark:text-gray-400">Please wait while we complete authentication.</p>
    {:else if status === 'success'}
      <div class="w-16 h-16 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mx-auto mb-4">
        <svg class="w-8 h-8 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <h2 class="text-xl font-semibold text-gray-900 dark:text-white mb-2">Success!</h2>
      <p class="text-gray-500 dark:text-gray-400">Redirecting to DocIntel...</p>
    {:else}
      <div class="w-16 h-16 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center mx-auto mb-4">
        <svg class="w-8 h-8 text-red-600 dark:text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </div>
      <h2 class="text-xl font-semibold text-gray-900 dark:text-white mb-2">Authentication Failed</h2>
      <p class="text-gray-500 dark:text-gray-400 mb-4">{errorMessage}</p>
      <a href="/" class="text-blue-600 dark:text-blue-400 hover:underline">Return to home</a>
    {/if}
  </div>
</div>
