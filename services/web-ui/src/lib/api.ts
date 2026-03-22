/**
 * Central API fetch wrapper.
 *
 * All HTTP methods (GET / POST / PUT / PATCH / DELETE) go through apiFetch.
 * It handles:
 *  - Injecting Authorization + X-Tenant-Id headers on every request
 *  - Token refresh via refresh_token grant before the request if the stored
 *    token is already expired
 *  - One automatic retry with a fresh token if the server returns 401
 *  - Central session-expired redirect (via triggerSessionExpired) when the
 *    retry also fails — fires regardless of which page made the call
 *
 * API_BASE comes from the runtime env injected by docker-entrypoint.sh via
 * getEnv() — no Node server required.
 */

import {
  userManager,
  getAccessToken,
  getTenantId,
  oidcUserToState,
  setAuthState,
  triggerSessionExpired,
  UNAUTHED_STATE,
} from '$lib/auth';
import { getEnv } from '$lib/env';

// Singleton in-flight promise for silent token refresh.
// Prevents 5 concurrent 401s from spawning 5 separate signinSilent() calls.
let _silentRefreshInFlight: Promise<void> | null = null;

function getApiBase(): string {
  return getEnv().PUBLIC_API_URL || 'http://localhost:8080';
}

function buildHeaders(
  base: HeadersInit | undefined,
  token: string | null,
  tenantId: string,
): Record<string, string> {
  const existing = base
    ? Object.fromEntries(new Headers(base).entries())
    : {};
  return {
    ...existing,
    'X-Tenant-Id': tenantId,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function apiFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = await getAccessToken();
  const tenantId = getTenantId();
  const headers = buildHeaders(options.headers, token, tenantId);

  // Use caller's AbortSignal (e.g. SSE AbortController) if provided, else 30s default.
  // SSE callers supply their own signal so they must not be capped at 30s.
  const signal = options.signal ?? AbortSignal.timeout(30_000);

  const response = await fetch(`${getApiBase()}${url}`, { ...options, headers, signal });

  if (response.status !== 401) return response;

  // Server returned 401 — force one silent refresh and retry.
  // Deduplicate: if a refresh is already in flight (e.g. 5 concurrent 401s),
  // wait for it rather than launching another signinSilent() call.
  if (!userManager) return response;

  try {
    if (!_silentRefreshInFlight) {
      _silentRefreshInFlight = userManager.signinSilent()
        .then(u => { if (u) setAuthState(oidcUserToState(u)); })
        .catch(() => {
          setAuthState(UNAUTHED_STATE);
          triggerSessionExpired();
        })
        .finally(() => { _silentRefreshInFlight = null; });
    }
    await _silentRefreshInFlight;

    const freshToken = await getAccessToken();
    const retryHeaders = buildHeaders(options.headers, freshToken, tenantId);
    const retryResponse = await fetch(`${getApiBase()}${url}`, { ...options, headers: retryHeaders, signal });
    if (retryResponse.status === 401) {
      setAuthState(UNAUTHED_STATE);
      triggerSessionExpired();
      throw new Error('Session expired');
    }
    return retryResponse;
  } catch (err) {
    if (err instanceof Error && err.message === 'Session expired') throw err;
    setAuthState(UNAUTHED_STATE);
    triggerSessionExpired();
    throw new Error('Session expired');
  }
}
