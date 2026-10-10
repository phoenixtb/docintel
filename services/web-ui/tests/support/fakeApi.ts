/**
 * A stand-in for the API gateway, served through Playwright request routing.
 *
 * Responses follow the shapes the real services return (document-service's
 * Spring page, rag-service's SSE events from src/api/main.py _serialize_sse).
 * Every request is recorded so tests can assert what the UI sent.
 */
import type { Page, Request, Route } from '@playwright/test';
import { API_ORIGIN, CORS_HEADERS } from './fakeIdp';

export interface Source {
  ref_id: number;
  chunk_id: string;
  document_id: string;
  filename: string;
  section: string;
  chunk_index: number;
  score: number;
  content: string;
  domain: string;
}

export interface StoredDocument {
  id: string;
  filename: string;
  contentType: string | null;
  fileSize: number;
  chunkCount: number;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  metadata: Record<string, string>;
  createdAt: string;
  updatedAt: string;
}

export interface RecordedRequest {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: string | null;
}

type Handler = (request: Request, url: URL) => Promise<Parameters<Route['fulfill']>[0]> | Parameters<Route['fulfill']>[0];

export const SAMPLE_SOURCE: Source = {
  ref_id: 1,
  chunk_id: 'chunk-1',
  document_id: 'doc-1',
  filename: 'leave-policy.pdf',
  section: 'Annual leave',
  chunk_index: 0,
  score: 0.91,
  content: 'Employees accrue 25 days of annual leave per year.',
  domain: 'hr_policy',
};

export function sseBody(events: Record<string, unknown>[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
}

/** The event sequence rag-service streams for an answered question. */
export function answerEvents(answer: string, sources: Source[] = [SAMPLE_SOURCE]): Record<string, unknown>[] {
  const words = answer.split(/(?<= )/);
  return [
    { metadata: { query_id: 'q-1', cache_hit: false, retrieval_mode: 'hybrid' } },
    { status: 'generating_answer' },
    ...words.map((token) => ({ token })),
    { sources, done: true },
  ];
}

export class FakeApi {
  readonly requests: RecordedRequest[] = [];
  readonly unhandled: string[] = [];
  documents: StoredDocument[] = [];
  queryEvents: Record<string, unknown>[] = answerEvents('Employees get 25 days of annual leave [1].');

  private readonly overrides = new Map<string, Handler>();

  /** Replace the response for one `METHOD /path` (path without query string). */
  on(method: string, path: string, handler: Handler): void {
    this.overrides.set(`${method} ${path}`, handler);
  }

  requestsTo(method: string, path: string): RecordedRequest[] {
    return this.requests.filter((r) => r.method === method && r.path === path);
  }

  async install(page: Page): Promise<void> {
    await page.route(`${API_ORIGIN}/**`, async (route, request) => {
      if (request.method() === 'OPTIONS') {
        return route.fulfill({ status: 204, headers: CORS_HEADERS });
      }
      const url = new URL(request.url());
      this.requests.push({
        method: request.method(),
        path: url.pathname,
        headers: request.headers(),
        body: request.postData(),
      });
      const handler = this.overrides.get(`${request.method()} ${url.pathname}`) ?? this.defaultHandler(request, url);
      if (!handler) {
        this.unhandled.push(`${request.method()} ${url.pathname}`);
        return route.fulfill({ status: 404, headers: CORS_HEADERS, json: { error: { code: 'NOT_FOUND' } } });
      }
      const response = await handler(request, url);
      return route.fulfill({ ...response, headers: { ...CORS_HEADERS, ...(response?.headers ?? {}) } });
    });
  }

  private defaultHandler(request: Request, url: URL): Handler | undefined {
    const key = `${request.method()} ${url.pathname}`;
    const now = new Date('2026-10-01T09:00:00Z').toISOString();
    switch (key) {
      case 'GET /api/v1/conversations':
        return () => ({ json: [] });
      case 'POST /api/v1/conversations':
        return () => ({
          json: { id: 'conv-1', title: 'New Conversation', created_at: now, updated_at: now, summary_upto_count: 0 },
        });
      case 'GET /api/v1/conversations/conv-1':
        return () => ({ json: { id: 'conv-1', title: 'New Conversation', messages: [], summary_upto_count: 0 } });
      case 'POST /api/v1/query/stream':
        return () => ({ contentType: 'text/event-stream', body: sseBody(this.queryEvents) });
      case 'POST /api/v1/feedback':
        return () => ({ json: { status: 'ok' } });
      case 'GET /api/v1/documents':
        return () => ({
          json: {
            content: this.documents,
            totalPages: 1,
            totalElements: this.documents.length,
            number: 0,
          },
        });
      case 'POST /api/v1/documents':
        return () => {
          const doc: StoredDocument = {
            id: `doc-${this.documents.length + 1}`,
            filename: 'handbook.txt',
            contentType: 'text/plain',
            fileSize: 42,
            chunkCount: 0,
            status: 'PENDING',
            metadata: { source: 'upload' },
            createdAt: now,
            updatedAt: now,
          };
          this.documents = [doc, ...this.documents];
          return { status: 202, json: doc };
        };
      case 'GET /api/v1/documents/events':
        // No in-flight documents: an empty, closed stream (the page reconnects after 1 s).
        return () => ({ contentType: 'text/event-stream', body: ': no events\n\n' });
      case 'GET /api/v1/vector-stats':
        return () => ({
          json: { total_vectors: 120, collections: { documents_acme: 120 }, tenant_stats: { hr_policy: 120 } },
        });
      case 'GET /api/v1/datasets':
        return () => ({
          json: {
            available_datasets: [
              { key: 'hr_policies', name: 'HR Policies', domain: 'hr_policy', description: 'HR policy Q&A pairs' },
            ],
          },
        });
      default:
        return undefined;
    }
  }
}

export function storedDocument(overrides: Partial<StoredDocument> = {}): StoredDocument {
  const now = new Date('2026-10-01T09:00:00Z').toISOString();
  return {
    id: 'doc-1',
    filename: 'leave-policy.pdf',
    contentType: 'application/pdf',
    fileSize: 182_000,
    chunkCount: 14,
    status: 'COMPLETED',
    metadata: { source: 'upload', domain: 'hr_policy' },
    createdAt: now,
    updatedAt: now,
    ...overrides,
  };
}
