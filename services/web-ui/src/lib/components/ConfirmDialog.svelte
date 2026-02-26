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
    <div class="relative z-10 w-full max-w-md rounded-2xl glass-dark border border-white/8 shadow-[0_20px_60px_rgba(0,0,0,0.7)] p-6 mx-4">
      <h2 id="confirm-title" class="text-base font-semibold text-slate-200 mb-2">{title}</h2>
      <p class="text-sm text-slate-500 mb-6">{message}</p>

      <div class="flex justify-end gap-3">
        <button
          onclick={oncancel}
          class="px-4 py-2 rounded-xl text-sm font-medium text-slate-400 bg-white/5 hover:bg-white/10 border border-white/5 hover:border-white/10 transition-all"
        >
          {cancelLabel}
        </button>
        <button
          onclick={onconfirm}
          class="px-4 py-2 rounded-xl text-sm font-medium transition-all {dangerous
            ? 'bg-red-600/80 hover:bg-red-500 text-white border border-red-500/30'
            : 'bg-emerald-600/80 hover:bg-emerald-500 text-white border border-emerald-500/30'}"
        >
          {confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}
