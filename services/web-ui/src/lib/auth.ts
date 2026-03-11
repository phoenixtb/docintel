/**
 * Authentication module using oidc-client-ts.
 *
 * Design:
 *  - Auth is always on. There is no PUBLIC_AUTH_ENABLED toggle.
 *  - UserManager is created eagerly at module load (browser-only) so events
 *    are always registered, including automaticSilentRenew timers.
 *  - Tokens are stored in localStorage so they survive tab close/reopen and
 *    are shared across all tabs.
 *  - offline_access scope gets us a refresh token; oidc-client-ts uses the
 *    refresh_token grant automatically when the access token expires.
 *  - getAccessToken() always returns a live token — it calls signinSilent()
 *    if the stored token is expired before returning.
 *  - onSessionExpired() lets hooks.client.ts register one central handler that
 *    fires for any unrecoverable auth failure (used by apiFetch).
 *  - Runtime env vars come from window.__DOCINTEL_ENV__ (injected by nginx
 *    entrypoint) via getEnv() — no Node server required.
 *  - Zitadel supports RP-Initiated Logout (post_logout_redirect_uri with id_token_hint)
 *    so signoutRedirect is used directly — no workaround needed.
 */

import { browser } from '$app/environment';
import { get, writable } from 'svelte/store';
import { UserManager, WebStorageStateStore, type User as OidcUser } from 'oidc-client-ts';
import { getEnv } from '$lib/env';

// ---- Types ----

export interface User {
  id: string;
  email: string;
  name: string;
  tenantId: string;
  role: string;
}

export interface AuthState {
  isAuthenticated: boolean;
  user: User | null;
  accessToken: string | null;
}

// ---- Default states ----

export const UNAUTHED_STATE: AuthState = {
  isAuthenticated: false,
  user: null,
  accessToken: null,
};

// ---- Reactive store (single source of truth) ----

export const authStore = writable<AuthState>(UNAUTHED_STATE);

export function setAuthState(state: AuthState) {
  authStore.set(state);
}

// ---- Session-expired callback (registered once by hooks.client.ts) ----

let _sessionExpiredHandler: (() => void) | null = null;

export function onSessionExpired(handler: () => void) {
  _sessionExpiredHandler = handler;
}

export function triggerSessionExpired() {
  if (_sessionExpiredHandler) {
    _sessionExpiredHandler();
  }
}

// ---- UserManager (eager singleton, browser-only) ----

function createUserManager(): UserManager | null {
  if (!browser) return null;
  const { PUBLIC_ZITADEL_ISSUER, PUBLIC_ZITADEL_CLIENT_ID } = getEnv();
  if (!PUBLIC_ZITADEL_CLIENT_ID) {
    console.warn('PUBLIC_ZITADEL_CLIENT_ID not set — auth will not work until setup-zitadel.sh runs.');
  }
  return new UserManager({
    authority: PUBLIC_ZITADEL_ISSUER,
    client_id: PUBLIC_ZITADEL_CLIENT_ID,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: 'code',
    // offline_access → refresh token grant; oidc-client-ts uses it automatically
    // Zitadel injects tenant_id and role via Action; no custom scopes needed
    scope: 'openid profile email offline_access',
    loadUserInfo: true,
    automaticSilentRenew: true,
    // localStorage: persists across tabs and browser restarts (tokens are bound
    // to the PKCE code verifier so there is no client_secret exposure risk)
    userStore: new WebStorageStateStore({ store: window.localStorage }),
  });
}

export const userManager: UserManager | null = createUserManager();

// Wire events immediately so automaticSilentRenew works from first load
if (userManager) {
  userManager.events.addUserLoaded((user) => {
    setAuthState(oidcUserToState(user));
  });
  userManager.events.addUserUnloaded(() => {
    setAuthState(UNAUTHED_STATE);
  });
  // addSilentRenewError and addAccessTokenExpired are registered in hooks.client.ts
  // so the central session-expired handler is already set up before they fire.
}

// ---- Helpers ----

export function oidcUserToState(user: OidcUser | null): AuthState {
  if (!user || user.expired) return UNAUTHED_STATE;
  const profile = user.profile as Record<string, unknown>;
  const tenantId =
    (profile.tenant_id as string) ||
    ((profile.tenant as Record<string, string>)?.tenant_id) ||
    'default';
  const role = (profile.role as string) || 'tenant_user';
  return {
    isAuthenticated: true,
    user: {
      id: profile.sub as string,
      email: (profile.email as string) || '',
      name: (profile.name as string) || (profile.preferred_username as string) || 'User',
      tenantId,
      role,
    },
    accessToken: user.access_token,
  };
}

// ---- Public API ----

export function getAuthState(): AuthState {
  return get(authStore);
}

export function getTenantId(): string {
  return get(authStore).user?.tenantId || 'default';
}

export function getRole(): string {
  return get(authStore).user?.role || 'tenant_user';
}

export function isPlatformAdmin(): boolean {
  return getRole() === 'platform_admin';
}

export function isTenantAdmin(): boolean {
  const role = getRole();
  return role === 'tenant_admin' || role === 'platform_admin';
}

/**
 * Returns a live access token, using the refresh token grant if needed.
 */
export async function getAccessToken(): Promise<string | null> {
  if (!userManager) return null;
  try {
    let user = await userManager.getUser();
    if (!user || user.expired) {
      user = await userManager.signinSilent();
    }
    return user?.access_token ?? null;
  } catch {
    return null;
  }
}

/**
 * Restore auth state on app load. If the stored token is expired, signinSilent()
 * uses the refresh token to get a new one silently.
 */
export async function restoreAuthState(): Promise<void> {
  if (!browser || !userManager) return;
  try {
    let user = await userManager.getUser();
    if (!user || user.expired) {
      try {
        user = await userManager.signinSilent();
      } catch {
        // Refresh token also expired — fall through to UNAUTHED_STATE
      }
    }
    setAuthState(oidcUserToState(user));
  } catch {
    setAuthState(UNAUTHED_STATE);
  }
}

export function login(): void {
  if (!browser || !userManager) return;
  userManager.signinRedirect();
}

export async function handleCallback(): Promise<AuthState | null> {
  if (!browser || !userManager) return null;
  try {
    const user = await userManager.signinRedirectCallback();
    const state = oidcUserToState(user);
    setAuthState(state);
    return state;
  } catch (err) {
    console.error('OIDC callback error:', err);
    return null;
  }
}

export async function logout(): Promise<void> {
  if (!browser) return;
  if (userManager) {
    try {
      const user = await userManager.getUser();
      setAuthState(UNAUTHED_STATE);
      if (user?.id_token) {
        // Zitadel RP-Initiated Logout: clears the session and redirects back to our app.
        // id_token_hint is required; post_logout_redirect_uri is respected by Zitadel.
        await userManager.signoutRedirect({
          id_token_hint: user.id_token,
          post_logout_redirect_uri: `${window.location.origin}/`,
        });
        return;
      }
    } catch (err) {
      console.error('Logout error:', err);
    }
    await userManager.removeUser();
  }
  setAuthState(UNAUTHED_STATE);
  window.location.replace('/');
}
