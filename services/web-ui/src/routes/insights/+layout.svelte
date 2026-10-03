<script lang="ts">
  import { page } from '$app/stores';

  let { children } = $props();

  const tabs = [
    { href: '/insights/usage', label: 'Usage' },
    { href: '/insights/quality', label: 'Quality' },
    { href: '/insights/feedback', label: 'Feedback' },
  ];

  function isActive(href: string): boolean {
    return $page.url.pathname.startsWith(href);
  }
</script>

<div class="h-full overflow-y-auto">
  <div class="max-w-5xl mx-auto p-6">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Insights</h1>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
        Usage, answer quality, and feedback for your tenant.
      </p>
    </div>

    <div class="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700">
      {#each tabs as tab}
        <a
          href={tab.href}
          class="px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors
            {isActive(tab.href)
              ? 'border-blue-600 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300'}"
        >
          {tab.label}
        </a>
      {/each}
    </div>

    {@render children()}
  </div>
</div>
