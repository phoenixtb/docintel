<script lang="ts">
  import { env } from '$env/dynamic/public';
  import { getAuthHeaders, getTenantId } from '$lib/auth';
  
  interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    sources?: Source[];
  }
  
  interface Source {
    document_id: string;
    filename: string;
    chunk_index: number;
    score: number;
  }
  
  let messages: Message[] = $state([]);
  let input = $state('');
  let isStreaming = $state(false);
  let currentResponse = $state('');
  let currentSources: Source[] = $state([]);
  
  const API_BASE = env.PUBLIC_API_URL || 'http://localhost:8080';
  
  function generateId(): string {
    return Math.random().toString(36).substring(2, 9);
  }
  
  async function sendMessage() {
    if (!input.trim() || isStreaming) return;
    
    const userMessage = input.trim();
    input = '';
    
    messages = [...messages, { 
      id: generateId(), 
      role: 'user', 
      content: userMessage 
    }];
    
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
          use_reranking: true,
          use_cache: true
        })
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
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
              
              if (data.token) {
                currentResponse += data.token;
              }
              
              if (data.sources) {
                currentSources = data.sources;
              }
              
              if (data.done) {
                // Stream complete
              }
            } catch {
              // Skip malformed JSON
            }
          }
        }
      }
      
      messages = [...messages, { 
        id: generateId(), 
        role: 'assistant', 
        content: currentResponse,
        sources: currentSources.length > 0 ? currentSources : undefined
      }];
      
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
</script>

<div class="flex flex-col h-full">
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
          <p class="text-gray-500 dark:text-gray-400 mb-4">Upload documents and ask questions to get AI-powered answers with source citations.</p>
          <a href="/documents" class="inline-flex items-center gap-2 text-blue-600 dark:text-blue-400 hover:underline">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            Go to Documents to upload or load sample data
          </a>
        </div>
      {/if}
      
      {#each messages as message (message.id)}
        <div class="flex {message.role === 'user' ? 'justify-end' : 'justify-start'}">
          <div class="max-w-2xl">
            <div class="px-4 py-3 rounded-2xl {message.role === 'user' 
              ? 'bg-blue-600 text-white' 
              : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm text-gray-900 dark:text-gray-100'}">
              <p class="whitespace-pre-wrap">{message.content}</p>
            </div>
            
            {#if message.sources && message.sources.length > 0}
              <div class="mt-2 flex flex-wrap gap-2">
                {#each message.sources as source}
                  <span class="inline-flex items-center px-2 py-1 rounded-md bg-gray-100 dark:bg-gray-700 text-xs text-gray-600 dark:text-gray-300">
                    📄 {source.filename}
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
