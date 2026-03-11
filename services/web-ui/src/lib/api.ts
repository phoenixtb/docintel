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
  setAuthState,
  triggerSessionExpired,
  UNAUTHED_STATE,
} from '$lib/auth';
import { getEnv } from '$lib/env';

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

  const response = await fetch(`${getApiBase()}${url}`, { ...options, headers });

  if (response.status !== 401) return response;

  // Server returned 401 — force one silent refresh and retry.
  if (!userManager) return response;

  try {
    const fresh = await userManager.signinSilent();
    setAuthState({
      isAuthenticated: true,
      user: null, // will be updated by the addUserLoaded event
      accessToken: fresh?.access_token ?? null,
    });
    const retryHeaders = buildHeaders(options.headers, fresh?.access_token ?? null, tenantId);
    const retryResponse = await fetch(`${getApiBase()}${url}`, { ...options, headers: retryHeaders });
    if (retryResponse.status === 401) {
      // Still 401 after refresh — session is truly dead
      setAuthState(UNAUTHED_STATE);
      triggerSessionExpired();
      throw new Error('Session expired');
    }
    return retryResponse;
  } catch (err) {
    if (err instanceof Error && err.message === 'Session expired') throw err;
    // signinSilent() itself threw (e.g. refresh token expired)
    setAuthState(UNAUTHED_STATE);
    triggerSessionExpired();
    throw new Error('Session expired');
  }
}
