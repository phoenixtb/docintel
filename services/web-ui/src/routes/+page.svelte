<script lang="ts">
  import { onMount } from 'svelte';
  import { env } from '$env/dynamic/public';
  import { getAuthHeaders, getTenantId, getAuthState } from '$lib/auth';
  
  interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    sources?: Source[];
  }
  
  interface Source {
    ref_id?: number;
    document_id: string;
    filename: string;
    section?: string;
    chunk_index?: number;
    score: number;
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
  let currentSources: Source[] = $state([]);
  
  // Conversation state
  let conversations: Conversation[] = $state([]);
  let activeConversationId: string | null = $state(null);
  let sidebarOpen = $state(true);
  let loadingConversations = $state(true);
  
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
  
  async function deleteConversation(convId: string, event: MouseEvent) {
    event.stopPropagation();
    if (!confirm('Delete this conversation?')) return;
    try {
      await fetch(
        `${API_BASE}/api/v1/conversations/${convId}?tenant_id=${getTenantId()}`,
        { method: 'DELETE', headers: { ...getAuthHeaders() } }
      );
      conversations = conversations.filter(c => c.id !== convId);
      if (activeConversationId === convId) {
        startNewChat();
      }
    } catch (e) {
      console.error('Failed to delete conversation:', e);
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
  
  async function sendMessage() {
    if (!input.trim() || isStreaming) return;
    
    const userMessage = input.trim();
    input = '';
    
    // Auto-create conversation on first message
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
    currentSources = [];
    
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
              if (data.token) currentResponse += data.token;
              if (data.sources) currentSources = data.sources;
            } catch { /* skip malformed */ }
          }
        }
      }
      
      messages = [...messages, { 
        id: generateId(), 
        role: 'assistant', 
        content: currentResponse,
        sources: currentSources.length > 0 ? currentSources : undefined
      }];
      
      // Update conversation title in sidebar (from first user message)
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
      currentSources = [];
    }
  }
  
  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }
  
  /** Parse content into segments for rendering [1], [2] as clickable refs */
  function parseContentWithRefs(content: string): { type: 'text' | 'ref'; content: string; refId?: number }[] {
    const segments: { type: 'text' | 'ref'; content: string; refId?: number }[] = [];
    const re = /\[(\d+)\]/g;
    let lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(content)) !== null) {
      if (m.index > lastIndex) {
        segments.push({ type: 'text', content: content.slice(lastIndex, m.index) });
      }
      segments.push({ type: 'ref', content: m[0], refId: parseInt(m[1], 10) });
      lastIndex = m.index + m[0].length;
    }
    if (lastIndex < content.length) {
      segments.push({ type: 'text', content: content.slice(lastIndex) });
    }
    return segments.length ? segments : [{ type: 'text', content }];
  }

  function scrollToSource(refId: number, messageId: string) {
    const el = document.querySelector(`[data-message-id="${messageId}"] [data-ref-id="${refId}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    el?.classList.add('ring-2', 'ring-blue-500');
    setTimeout(() => el?.classList.remove('ring-2', 'ring-blue-500'), 1500);
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

<div class="flex h-full">
  <!-- Sidebar: Conversations -->
  <div class="flex-shrink-0 {sidebarOpen ? 'w-72' : 'w-0'} transition-all duration-200 overflow-hidden border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
    <div class="flex flex-col h-full w-72">
      <!-- Sidebar header -->
      <div class="p-3 border-b border-gray-200 dark:border-gray-700">
        <button
          onclick={startNewChat}
          class="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg border border-gray-300 dark:border-gray-600 
            text-sm font-medium text-gray-700 dark:text-gray-300
            hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          New Chat
        </button>
      </div>
      
      <!-- Conversation list -->
      <div class="flex-1 overflow-y-auto">
        {#if loadingConversations}
          <div class="p-4 text-center text-sm text-gray-400">Loading...</div>
        {:else if conversations.length === 0}
          <div class="p-4 text-center text-sm text-gray-400 dark:text-gray-500">
            No conversations yet
          </div>
        {:else}
          {#each conversations as conv (conv.id)}
            <div
              onclick={() => loadConversation(conv.id)}
              onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); loadConversation(conv.id); } }}
              role="button"
              tabindex="0"
              class="w-full text-left px-3 py-3 flex items-start gap-2 group cursor-pointer
                hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors
                {activeConversationId === conv.id ? 'bg-blue-50 dark:bg-blue-900/20 border-r-2 border-blue-600' : ''}"
            >
              <svg class="w-4 h-4 mt-0.5 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <div class="flex-1 min-w-0">
                <p class="text-sm text-gray-900 dark:text-gray-100 truncate">{conv.title}</p>
                <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{formatDate(conv.updated_at)}</p>
              </div>
              <button
                onclick={(e) => deleteConversation(conv.id, e)}
                class="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-opacity"
                title="Delete"
              >
                <svg class="w-3.5 h-3.5 text-gray-400 hover:text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          {/each}
        {/if}
      </div>
    </div>
  </div>
  
  <!-- Main chat area -->
  <div class="flex-1 flex flex-col min-w-0">
    <!-- Toggle sidebar button -->
    <div class="flex items-center gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      <button
        onclick={() => sidebarOpen = !sidebarOpen}
        class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
        title="{sidebarOpen ? 'Hide' : 'Show'} sidebar"
      >
        <svg class="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      {#if activeConversationId}
        <span class="text-sm text-gray-500 dark:text-gray-400 truncate">
          {conversations.find(c => c.id === activeConversationId)?.title || 'Chat'}
        </span>
      {/if}
    </div>
    
    <!-- Messages -->
    <div class="flex-1 overflow-y-auto p-6">
      <div class="max-w-3xl mx-auto space-y-6">
        {#if messages.length === 0}
          <div class="text-center py-12">
            <div class="w-16 h-16 bg-blue-100 dark:bg-blue-900/30 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg class="w-8 h-8 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" 
                  d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
              </svg>
            </div>
            <h2 class="text-lg font-medium text-gray-900 dark:text-white mb-2">Ask about your documents</h2>
            <p class="text-gray-500 dark:text-gray-400 mb-2">Upload documents or load sample data, then ask questions to get AI-powered answers with source citations.</p>
            <p class="text-xs text-amber-600 dark:text-amber-400 mb-4">Tip: Sample datasets are for demo — they may not cover all topics.</p>
            <a href="/documents" class="inline-flex items-center gap-2 text-blue-600 dark:text-blue-400 hover:underline">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Go to Documents to upload or load sample data
            </a>
          </div>
        {/if}
        
        {#each messages as message (message.id)}
          <div class="flex {message.role === 'user' ? 'justify-end' : 'justify-start'}" data-message-id={message.id}>
            <div class="max-w-2xl">
              <div class="px-4 py-3 rounded-2xl {message.role === 'user' 
                ? 'bg-blue-600 text-white' 
                : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm text-gray-900 dark:text-gray-100'}">
                {#if message.role === 'assistant' && message.sources && message.sources.length > 0}
                  <p class="whitespace-pre-wrap">
                    {#each parseContentWithRefs(message.content) as seg}
                      {#if seg.type === 'ref'}
                        <button
                          type="button"
                          onclick={() => scrollToSource(seg.refId!, message.id)}
                          class="inline-flex align-baseline px-0.5 mx-0.5 rounded text-blue-600 dark:text-blue-400 hover:underline font-medium cursor-pointer"
                          title="Jump to source"
                        >{seg.content}</button>
                      {:else}
                        {seg.content}
                      {/if}
                    {/each}
                  </p>
                {:else}
                  <p class="whitespace-pre-wrap">{message.content}</p>
                {/if}
              </div>
              
              {#if message.sources && message.sources.length > 0}
                <div class="mt-2 flex flex-wrap gap-2">
                  {#each message.sources as source, i}
                    <span
                      data-ref-id={source.ref_id ?? i + 1}
                      class="inline-flex items-center px-2 py-1 rounded-md bg-gray-100 dark:bg-gray-700 text-xs text-gray-600 dark:text-gray-300 transition-all"
                    >
                      <svg class="w-3 h-3 mr-1 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      {source.filename}{#if source.section} ({source.section}){/if}
                    </span>
                  {/each}
                </div>
              {/if}
            </div>
          </div>
        {/each}
        
        {#if isStreaming && currentResponse}
          <div class="flex justify-start">
            <div class="max-w-2xl px-4 py-3 rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm">
              <p class="whitespace-pre-wrap text-gray-900 dark:text-gray-100">{currentResponse}<span class="animate-pulse text-blue-600 dark:text-blue-400">▌</span></p>
            </div>
          </div>
        {/if}
        
        {#if isStreaming && !currentResponse}
          <div class="flex justify-start">
            <div class="px-4 py-3 rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm">
              <div class="flex items-center gap-2 text-gray-500 dark:text-gray-400">
                <div class="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style="animation-delay: 0ms"></div>
                <div class="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style="animation-delay: 150ms"></div>
                <div class="w-2 h-2 bg-blue-600 rounded-full animate-bounce" style="animation-delay: 300ms"></div>
              </div>
            </div>
          </div>
        {/if}
      </div>
    </div>
    
    <!-- Input -->
    <div class="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 p-4">
      <div class="max-w-3xl mx-auto">
        <div class="flex gap-3 items-center">
          <div class="flex-1">
            <textarea
              bind:value={input}
              onkeydown={handleKeydown}
              placeholder="Ask a question about your documents..."
              rows="1"
              class="w-full px-4 py-3 border border-gray-300 dark:border-gray-600 rounded-xl resize-none 
                bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100
                placeholder-gray-400 dark:placeholder-gray-500
                focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              disabled={isStreaming}
            ></textarea>
          </div>
          <button
            onclick={sendMessage}
            disabled={isStreaming || !input.trim()}
            class="p-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex-shrink-0"
          >
            {#if isStreaming}
              <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            {:else}
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            {/if}
          </button>
        </div>
        <p class="text-xs text-gray-400 dark:text-gray-500 mt-2 text-center">
          Press Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  </div>
</div>
