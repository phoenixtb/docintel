<script lang="ts">
  interface Props {
    open: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    dangerous?: boolean;
    onconfirm: () => void;
    oncancel: () => void;
  }

  let {
    open,
    title,
    message,
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    dangerous = false,
    onconfirm,
    oncancel,
  }: Props = $props();

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') oncancel();
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center"
    role="dialog"
    aria-modal="true"
    aria-labelledby="confirm-title"
    onkeydown={handleKeydown}
  >
    <!-- Backdrop -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="absolute inset-0 bg-black/50 backdrop-blur-sm"
      onclick={oncancel}
    ></div>

    <!-- Dialog -->
    <div class="relative z-10 w-full max-w-md rounded-xl bg-gray-900 border border-gray-700 shadow-2xl p-6 mx-4">
      <h2 id="confirm-title" class="text-lg font-semibold text-white mb-2">{title}</h2>
      <p class="text-sm text-gray-400 mb-6">{message}</p>

      <div class="flex justify-end gap-3">
        <button
          onclick={oncancel}
          class="px-4 py-2 rounded-lg text-sm font-medium text-gray-300 bg-gray-800 hover:bg-gray-700 transition-colors"
        >
          {cancelLabel}
        </button>
        <button
          onclick={onconfirm}
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors {dangerous
            ? 'bg-red-600 hover:bg-red-500 text-white'
            : 'bg-blue-600 hover:bg-blue-500 text-white'}"
        >
          {confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}
