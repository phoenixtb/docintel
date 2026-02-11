/**
 * Authentication module using oidc-client-ts
 * 
 * Uses the standard UserManager from oidc-client-ts for
 * OAuth2/OIDC flows with Authentik (or any OIDC provider).
 * 
 * Two modes:
 * 1. Dev mode (PUBLIC_AUTH_ENABLED=false): No auth, uses default tenant
 * 2. Auth mode (PUBLIC_AUTH_ENABLED=true): Full OIDC via Authentik
 */

import { browser } from '$app/environment';
import { env } from '$env/dynamic/public';
import { UserManager, WebStorageStateStore, type User as OidcUser } from 'oidc-client-ts';

// Configuration
const AUTH_ENABLED = env.PUBLIC_AUTH_ENABLED === 'true';
const AUTHENTIK_AUTHORITY = env.PUBLIC_AUTHENTIK_ISSUER || 'http://localhost:9090/application/o/docintel/';
const CLIENT_ID = env.PUBLIC_AUTHENTIK_CLIENT_ID || 'docintel';

// ---- Types ----

export interface User {
  id: string;
  email: string;
  name: string;
  tenantId: string;
}

export interface AuthState {
  isAuthenticated: boolean;
  user: User | null;
  accessToken: string | null;
}

// ---- Default dev state ----

const DEV_STATE: AuthState = {
  isAuthenticated: true,
  user: {
    id: 'dev-user',
    email: 'dev@docintel.local',
    name: 'Developer',
    tenantId: 'default',
  },
  accessToken: null,
};

const UNAUTHED_STATE: AuthState = {
  isAuthenticated: false,
  user: null,
  accessToken: null,
};

// ---- UserManager singleton ----

let userManager: UserManager | null = null;
let cachedState: AuthState = AUTH_ENABLED ? UNAUTHED_STATE : DEV_STATE;

function getUserManager(): UserManager {
  if (!userManager && browser) {
    userManager = new UserManager({
      authority: AUTHENTIK_AUTHORITY,
      client_id: CLIENT_ID,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: 'code',
      scope: 'openid profile email tenant',
      automaticSilentRenew: true,
      userStore: new WebStorageStateStore({ store: sessionStorage }),
    });

    // Listen for token expiry / silent renew
    userManager.events.addUserLoaded((user) => {
      cachedState = oidcUserToState(user);
    });
    userManager.events.addUserUnloaded(() => {
      cachedState = AUTH_ENABLED ? UNAUTHED_STATE : DEV_STATE;
    });
    userManager.events.addAccessTokenExpired(() => {
      cachedState = AUTH_ENABLED ? UNAUTHED_STATE : DEV_STATE;
    });
  }
  return userManager!;
}

function oidcUserToState(user: OidcUser | null): AuthState {
  if (!user || user.expired) {
    return AUTH_ENABLED ? UNAUTHED_STATE : DEV_STATE;
  }
  const profile = user.profile;
  // Extract tenant_id: try direct claim, then nested tenant object
  const tenantId =
    (profile as Record<string, unknown>).tenant_id as string ||
    ((profile as Record<string, unknown>).tenant as Record<string, string>)?.tenant_id ||
    'default';

  return {
    isAuthenticated: true,
    user: {
      id: profile.sub,
      email: profile.email || '',
      name: profile.name || profile.preferred_username || 'User',
      tenantId,
    },
    accessToken: user.access_token,
  };
}

// ---- Public API ----

export function isAuthEnabled(): boolean {
  return AUTH_ENABLED;
}

export function getAuthState(): AuthState {
  return { ...cachedState };
}

export function getTenantId(): string {
  return cachedState.user?.tenantId || 'default';
}

export function getAccessToken(): string | null {
  return cachedState.accessToken;
}

/**
 * Restore auth state from oidc-client-ts session store.
 * Call once on app mount.
 */
export async function restoreAuthState(): Promise<void> {
  if (!AUTH_ENABLED || !browser) return;
  try {
    const user = await getUserManager().getUser();
    cachedState = oidcUserToState(user);
  } catch {
    cachedState = UNAUTHED_STATE;
  }
}

/**
 * Redirect to the OIDC provider login page.
 */
export function login(): void {
  if (!AUTH_ENABLED || !browser) return;
  getUserManager().signinRedirect();
}

/**
 * Handle the OIDC callback (exchange code for tokens).
 * Returns the authenticated user state on success, null on failure.
 */
export async function handleCallback(): Promise<AuthState | null> {
  if (!browser) return null;
  try {
    const user = await getUserManager().signinRedirectCallback();
    cachedState = oidcUserToState(user);
    return cachedState;
  } catch (err) {
    console.error('OIDC callback error:', err);
    return null;
  }
}

/**
 * Log out and redirect to OIDC provider end-session.
 */
export function logout(): void {
  if (!browser) return;
  if (AUTH_ENABLED) {
    getUserManager().signoutRedirect();
  }
  cachedState = AUTH_ENABLED ? UNAUTHED_STATE : DEV_STATE;
}

/**
 * Build headers for authenticated API requests.
 * Includes Authorization bearer token and X-Tenant-Id.
 */
export function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Tenant-Id': getTenantId(),
  };
  if (cachedState.accessToken) {
    headers['Authorization'] = `Bearer ${cachedState.accessToken}`;
  }
  return headers;
}
