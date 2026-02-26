<script lang="ts">
  import { onMount } from 'svelte';
  import { env } from '$env/dynamic/public';
  import { getAuthHeaders, getTenantId, getAuthState } from '$lib/auth';
  import { toast } from 'svelte-sonner';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  import MessageBubble from '$lib/components/MessageBubble.svelte';

  interface Source {
    ref_id?: number;
    document_id: string;
    filename: string;
    section?: string;
    chunk_index?: number;
    score: number;
    content?: string;
  }

  interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    thinking?: string;
    sources?: Source[];
    liked?: boolean | null;
    queryId?: string;
    routedDomain?: string;
  }
  
  interface Conversation {
    id: string;
    title: string;
    updated_at: string;
  }
  
  // Chat state
  let messages: Message[] = $state([]);
  let input = $state('');
  let isStreaming = $state(false);
  let currentResponse = $state('');
  let currentThinking = $state('');
  let currentSources: Source[] = $state([]);
  let currentQueryId = $state('');
  let currentRoutedDomain = $state<string | null>(null);
  
  // Conversation state
  let conversations: Conversation[] = $state([]);
  let activeConversationId: string | null = $state(null);
  // true = full sidebar, false = rail mode
  let sidebarOpen = $state(true);
  let loadingConversations = $state(true);

  // Confirm dialog state
  let confirmOpen = $state(false);
  let confirmConvId: string | null = $state(null);
  
  const API_BASE = env.PUBLIC_API_URL || 'http://localhost:8080';
  
  function generateId(): string {
    return Math.random().toString(36).substring(2, 9);
  }
  
  // ==========================================================================
  // Conversation management
  // ==========================================================================
  
  async function fetchConversations() {
    try {
      const tenantId = getTenantId();
      const userId = getAuthState().user?.id || '';
      const res = await fetch(
        `${API_BASE}/api/v1/conversations?tenant_id=${tenantId}&user_id=${userId}`,
        { headers: { ...getAuthHeaders() } }
      );
      if (res.ok) {
        conversations = await res.json();
      }
    } catch (e) {
      console.error('Failed to fetch conversations:', e);
    } finally {
      loadingConversations = false;
    }
  }
  
  async function createConversation(): Promise<string | null> {
    try {
      const res = await fetch(`${API_BASE}/api/v1/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          tenant_id: getTenantId(),
          user_id: getAuthState().user?.id || '',
        }),
      });
      if (res.ok) {
        const conv = await res.json();
        conversations = [conv, ...conversations];
        return conv.id;
      }
    } catch (e) {
      console.error('Failed to create conversation:', e);
    }
    return null;
  }
  
  async function loadConversation(convId: string) {
    activeConversationId = convId;
    messages = [];
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/conversations/${convId}?tenant_id=${getTenantId()}`,
        { headers: { ...getAuthHeaders() } }
      );
      if (res.ok) {
        const data = await res.json();
        messages = (data.messages || []).map((m: any) => ({
          id: m.id,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          sources: m.sources || undefined,
        }));
      }
    } catch (e) {
      console.error('Failed to load conversation:', e);
    }
  }
  
  function deleteConversation(convId: string, event: MouseEvent) {
    event.stopPropagation();
    confirmConvId = convId;
    confirmOpen = true;
  }

  async function doDeleteConversation() {
    if (!confirmConvId) return;
    const convId = confirmConvId;
    confirmOpen = false;
    confirmConvId = null;
    try {
      await fetch(
        `${API_BASE}/api/v1/conversations/${convId}?tenant_id=${getTenantId()}`,
        { method: 'DELETE', headers: { ...getAuthHeaders() } }
      );
      conversations = conversations.filter(c => c.id !== convId);
      if (activeConversationId === convId) {
        startNewChat();
      }
      toast.success('Conversation deleted');
    } catch (e) {
      console.error('Failed to delete conversation:', e);
      toast.error('Failed to delete conversation');
    }
  }
  
  function startNewChat() {
    activeConversationId = null;
    messages = [];
    input = '';
  }
  
  // ==========================================================================
  // Send message
  // ==========================================================================
  
  async function sendMessage(overrideText?: string) {
    if (isStreaming) return;
    if (!overrideText && !input.trim()) return;

    const userMessage = overrideText ?? input.trim();
    if (!overrideText) input = '';
    
    if (!activeConversationId) {
      const newId = await createConversation();
      if (!newId) {
        messages = [...messages, { id: generateId(), role: 'assistant', content: 'Failed to create conversation. Please try again.' }];
        return;
      }
      activeConversationId = newId;
    }
    
    messages = [...messages, { id: generateId(), role: 'user', content: userMessage }];
    
    isStreaming = true;
    currentResponse = '';
    currentThinking = '';
    currentSources = [];
    currentQueryId = '';
    
    try {
      const response = await fetch(`${API_BASE}/api/v1/query/stream`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ 
          question: userMessage,
          tenant_id: getTenantId(),
          conversation_id: activeConversationId,
          use_reranking: true,
          use_cache: true
        })
      });
      
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.metadata?.query_id) currentQueryId = data.metadata.query_id;
              if (data.routing?.domain) currentRoutedDomain = data.routing.domain;
              if (data.thinking_token) currentThinking += data.thinking_token;
              if (data.token) currentResponse += data.token;
              if (data.sources) currentSources = data.sources;
              if (data.error) throw new Error(data.error);
              if (data.done) { reader.cancel(); break; }
            } catch { /* skip malformed */ }
          }
        }
      }
      
      messages = [...messages, {
        id: generateId(),
        role: 'assistant',
        content: currentResponse,
        thinking: currentThinking || undefined,
        sources: currentSources.length > 0 ? currentSources : undefined,
        liked: null,
        queryId: currentQueryId || undefined,
        routedDomain: currentRoutedDomain || undefined,
      }];
      
      const conv = conversations.find(c => c.id === activeConversationId);
      if (conv && conv.title === 'New Conversation') {
        conv.title = userMessage.slice(0, 80) + (userMessage.length > 80 ? '...' : '');
        conversations = [...conversations];
      }
      
    } catch (error) {
      console.error('Stream error:', error);
      messages = [...messages, { 
        id: generateId(), 
        role: 'assistant', 
        content: 'Sorry, an error occurred. Please try again.' 
      }];
    } finally {
      isStreaming = false;
      currentResponse = '';
      currentThinking = '';
      currentSources = [];
      currentQueryId = '';
      currentRoutedDomain = null;
    }
  }
  
  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  async function setFeedback(messageId: string, liked: boolean) {
    const msg = messages.find(m => m.id === messageId);
    if (!msg) return;
    const newLiked = msg.liked === liked ? null : liked;
    messages = messages.map(m => m.id === messageId ? { ...m, liked: newLiked } : m);
    if (newLiked === null) return;
    try {
      await fetch(`${API_BASE}/api/v1/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          query_id: msg.queryId ?? '',
          tenant_id: getTenantId(),
          user_id: getAuthState().user?.id ?? '',
          liked: newLiked,
        }),
      });
    } catch (e) {
      console.error('Feedback POST failed:', e);
    }
  }

  async function regenerate(messageId: string) {
    const idx = messages.findIndex(m => m.id === messageId);
    if (idx === -1) return;
    const userMsg = [...messages].slice(0, idx).findLast(m => m.role === 'user');
    if (!userMsg) return;
    messages = messages.slice(0, idx);
    await sendMessage(userMsg.content);
  }

  function formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    return d.toLocaleDateString();
  }
  
  onMount(() => {
    fetchConversations();
  });
</script>

<div class="flex h-full gap-0">

  <!-- ═══════════════════════════════════════════════════════════════════
       Sidebar: floating panel, toggles between full (w-72) and rail (w-14)
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="flex-shrink-0 transition-all duration-300 flex flex-col
    {sidebarOpen ? 'w-[272px]' : 'w-[56px]'} p-2 pr-1">

    <div class="flex flex-col h-full glass-dark rounded-2xl overflow-hidden shadow-glass">

      <!-- ── Sidebar header row: hamburger (left) + new chat (right) ── -->
      <div class="flex-shrink-0 flex items-center border-b border-white/5
        {sidebarOpen ? 'px-2.5 py-2.5 justify-between' : 'px-2 py-2.5 justify-center'}">

        <!-- Hamburger toggle -->
        <button
          onclick={() => sidebarOpen = !sidebarOpen}
          title="{sidebarOpen ? 'Collapse' : 'Expand'} sidebar"
          class="p-1.5 rounded-lg hover:bg-white/5 text-slate-500 hover:text-emerald-400 transition-colors flex-shrink-0"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>

        <!-- New Chat — text button in full mode -->
        {#if sidebarOpen}
          <button
            onclick={startNewChat}
            title="New Chat"
            class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-medium
              text-emerald-400 ring-1 ring-emerald-500/20 hover:ring-emerald-400/40
              hover:bg-emerald-500/5 transition-all"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
            </svg>
            New Chat
          </button>
        {/if}
      </div>

      <!-- ── Rail mode body (new chat icon + divider + conv icons) ── -->
      {#if !sidebarOpen}
        <!-- New Chat icon row -->
        <div class="flex-shrink-0 flex justify-center py-2 border-b border-white/5">
          <button
            onclick={startNewChat}
            title="New Chat"
            class="w-8 h-8 rounded-lg flex items-center justify-center
              text-slate-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
            </svg>
          </button>
        </div>

        <!-- Rail conversation icons -->
        <div class="flex-1 overflow-y-auto flex flex-col items-center py-2 gap-1">
          {#if loadingConversations}
            <div class="w-4 h-4 border border-emerald-500/30 border-t-emerald-400 rounded-full animate-spin mt-2"></div>
          {:else}
            {#each conversations as conv (conv.id)}
              <button
                onclick={() => loadConversation(conv.id)}
                title={conv.title}
                class="w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-bold transition-all
                  {activeConversationId === conv.id
                    ? 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30'
                    : 'text-slate-600 hover:bg-white/5 hover:text-slate-300'}"
              >
                {conv.title.charAt(0).toUpperCase()}
              </button>
            {/each}
          {/if}
        </div>
      {/if}

      <!-- ── Full sidebar: conversation list ── -->
      {#if sidebarOpen}
        <div class="flex-1 overflow-y-auto py-2 min-h-0">
          {#if loadingConversations}
            <div class="py-4 flex justify-center">
              <div class="w-4 h-4 border border-emerald-500/30 border-t-emerald-400 rounded-full animate-spin"></div>
            </div>
          {:else if conversations.length === 0}
            <div class="py-6 text-center text-xs text-slate-600">
              No conversations yet
            </div>
          {:else}
            {#each conversations as conv (conv.id)}
              <div
                onclick={() => loadConversation(conv.id)}
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); loadConversation(conv.id); } }}
                role="button"
                tabindex="0"
                class="group flex items-start gap-2 mx-1.5 px-2.5 py-2 rounded-lg cursor-pointer transition-all
                  {activeConversationId === conv.id
                    ? 'bg-emerald-500/10 border-l-2 border-emerald-400 text-emerald-300'
                    : 'hover:bg-white/5 text-slate-400 hover:text-slate-200'}"
              >
                <svg class="w-3.5 h-3.5 mt-0.5 flex-shrink-0 {activeConversationId === conv.id ? 'text-emerald-400' : 'text-slate-600'}"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                <div class="flex-1 min-w-0">
                  <p class="text-xs font-medium truncate">{conv.title}</p>
                  <p class="text-[10px] text-slate-600 mt-0.5">{formatDate(conv.updated_at)}</p>
                </div>
                <button
                  onclick={(e) => deleteConversation(conv.id, e)}
                  class="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-500/10 transition-all flex-shrink-0"
                  title="Delete"
                >
                  <svg class="w-3 h-3 text-slate-500 hover:text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            {/each}
          {/if}
        </div>
      {/if}

    </div>
  </div>

  <!-- ═══════════════════════════════════════════════════════════════════
       Main chat area — no extra bar, hamburger lives in sidebar now
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="flex-1 flex flex-col min-w-0">

    <!-- Messages -->
    <div class="flex-1 overflow-y-auto px-4 pt-4 pb-2">
      <div class="max-w-3xl mx-auto space-y-6">

        <!-- Empty state -->
        {#if messages.length === 0}
          <div class="text-center py-20 animate-fade-in">
            <div class="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-5
              bg-emerald-500/10 ring-1 ring-emerald-500/30 shadow-[0_0_32px_rgba(16,185,129,0.15)]">
              <svg class="w-8 h-8 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                  d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
              </svg>
            </div>
            <h2 class="text-lg font-semibold text-slate-200 mb-2">Ask about your documents</h2>
            <p class="text-sm text-slate-500 mb-2 max-w-md mx-auto">Upload documents or load sample data, then ask questions to get AI-powered answers with source citations.</p>
            <p class="text-xs text-amber-500/70 mb-5">Tip: Sample datasets are for demo — they may not cover all topics.</p>
            <a href="/documents" class="inline-flex items-center gap-1.5 text-sm text-emerald-400 hover:text-emerald-300 hover:underline transition-colors">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Go to Documents to upload or load sample data
            </a>
          </div>
        {/if}

        <!-- Message list -->
        {#each messages as message (message.id)}
          <div data-message-id={message.id}>
            {#if message.role === 'user'}
              <div class="flex justify-end">
                <div class="max-w-2xl px-4 py-3 rounded-2xl
                  bg-emerald-600/75 backdrop-blur-sm text-white
                  shadow-[0_4px_20px_rgba(16,185,129,0.2)]
                  border border-emerald-500/30">
                  <p class="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
                </div>
              </div>
            {:else}
              <MessageBubble
                {message}
                onRegenerate={() => regenerate(message.id)}
                onFeedback={(liked) => setFeedback(message.id, liked)}
              />
            {/if}
          </div>
        {/each}

        <!-- Streaming response -->
        {#if isStreaming}
          <div class="flex justify-start">
            <div class="max-w-3xl w-full space-y-2">
              {#if currentThinking}
                <details open class="rounded-xl border border-dashed border-emerald-500/20 bg-emerald-950/20 backdrop-blur-sm text-xs">
                  <summary class="flex items-center gap-2 px-3 py-2 cursor-pointer select-none text-emerald-500/70 hover:text-emerald-400 list-none">
                    <span class="animate-pulse w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.8)]"></span>
                    <span class="font-medium">Thinking...</span>
                  </summary>
                  <pre class="px-3 pb-3 pt-1 font-mono text-xs text-slate-500 whitespace-pre-wrap">{currentThinking}</pre>
                </details>
              {/if}
              {#if currentRoutedDomain}
                <div class="flex items-center gap-1.5 px-1 text-xs text-slate-500">
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                  </svg>
                  <span>Searching in</span>
                  <span class="px-1.5 py-0.5 rounded-full font-medium text-xs
                    { currentRoutedDomain === 'hr_policy' ? 'bg-purple-900/50 text-purple-300'
                    : currentRoutedDomain === 'technical' ? 'bg-cyan-900/50 text-cyan-300'
                    : currentRoutedDomain === 'contracts' ? 'bg-amber-900/50 text-amber-300'
                    : 'bg-white/5 text-slate-400' }">
                    { currentRoutedDomain === 'hr_policy' ? 'HR'
                    : currentRoutedDomain === 'technical' ? 'Tech'
                    : currentRoutedDomain === 'contracts' ? 'Contract'
                    : currentRoutedDomain }
                  </span>
                  <span class="text-slate-700">(auto-routed)</span>
                </div>
              {/if}
              {#if currentResponse}
                <div class="px-4 py-3 rounded-2xl glass border border-emerald-500/10 text-slate-200 text-sm leading-relaxed">
                  <p class="whitespace-pre-wrap">{currentResponse}<span class="animate-pulse text-emerald-400 ml-px">▌</span></p>
                </div>
              {/if}
            </div>
          </div>
        {/if}

        <!-- Loading dots (before first token) -->
        {#if isStreaming && !currentResponse && !currentThinking}
          <div class="flex justify-start">
            <div class="px-4 py-3 rounded-2xl glass border border-emerald-500/10">
              <div class="flex items-center gap-1.5">
                <div class="w-2 h-2 bg-emerald-400 rounded-full animate-bounce shadow-[0_0_6px_rgba(16,185,129,0.6)]" style="animation-delay: 0ms"></div>
                <div class="w-2 h-2 bg-emerald-400 rounded-full animate-bounce shadow-[0_0_6px_rgba(16,185,129,0.6)]" style="animation-delay: 150ms"></div>
                <div class="w-2 h-2 bg-emerald-400 rounded-full animate-bounce shadow-[0_0_6px_rgba(16,185,129,0.6)]" style="animation-delay: 300ms"></div>
              </div>
            </div>
          </div>
        {/if}

      </div>
    </div>

    <!-- ── Floating glass input ── -->
    <div class="px-4 py-3">
      <div class="max-w-3xl mx-auto">
        <div class="glass-input rounded-2xl px-4 py-3 flex items-end gap-3">
          <textarea
            bind:value={input}
            onkeydown={handleKeydown}
            placeholder="Ask a question about your documents..."
            rows="1"
            class="flex-1 bg-transparent text-slate-200 placeholder-slate-600
              text-sm leading-relaxed resize-none
              focus:outline-none min-h-[24px] max-h-40"
            style="field-sizing: content;"
            disabled={isStreaming}
          ></textarea>
          <button
            onclick={() => sendMessage()}
            disabled={isStreaming || !input.trim()}
            class="flex-shrink-0 p-2.5 rounded-xl transition-all duration-200
              {isStreaming || !input.trim()
                ? 'bg-white/5 text-slate-600 cursor-not-allowed'
                : 'bg-emerald-500 hover:bg-emerald-400 text-white shadow-[0_0_16px_rgba(16,185,129,0.4)] hover:shadow-[0_0_24px_rgba(16,185,129,0.6)]'}"
          >
            {#if isStreaming}
              <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            {:else}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            {/if}
          </button>
        </div>
        <p class="text-xs text-slate-700 mt-2 text-center">Enter to send · Shift+Enter for new line</p>
      </div>
    </div>

  </div>
</div>

<ConfirmDialog
  open={confirmOpen}
  title="Delete conversation?"
  message="This conversation will be permanently deleted."
  confirmLabel="Delete"
  dangerous={true}
  onconfirm={doDeleteConversation}
  oncancel={() => { confirmOpen = false; confirmConvId = null; }}
/>
