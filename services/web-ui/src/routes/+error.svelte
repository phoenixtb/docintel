<script lang="ts">
  import { page } from '$app/stores';
</script>

<div class="flex h-screen items-center justify-center bg-gray-950 text-white px-6">
  <div class="max-w-md w-full text-center space-y-6">
    <div class="w-20 h-20 rounded-full flex items-center justify-center mx-auto
      bg-red-500/10 ring-1 ring-red-500/30">
      <svg class="w-10 h-10 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
          d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
    </div>

    <div>
      <h1 class="text-5xl font-bold text-red-400 mb-2">{$page.status}</h1>
      <p class="text-lg font-semibold text-slate-200">{$page.error?.message || 'Something went wrong'}</p>
      <p class="text-sm text-slate-500 mt-2">
        {#if $page.status === 404}
          The page you're looking for doesn't exist.
        {:else if $page.status === 403}
          You don't have permission to access this resource.
        {:else}
          An unexpected error occurred. Please try again.
        {/if}
      </p>
    </div>

    <div class="flex items-center justify-center gap-3">
      <a
        href="/"
        class="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium transition-colors"
      >
        Go Home
      </a>
      <button
        onclick={() => history.back()}
        class="px-5 py-2.5 rounded-xl border border-slate-700 hover:border-slate-500 text-slate-300 text-sm font-medium transition-colors"
      >
        Go Back
      </button>
    </div>
  </div>
</div>
