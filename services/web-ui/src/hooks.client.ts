/**
 * SvelteKit client-side hook — runs once before any route loads.
 *
 * Auth is always on (no PUBLIC_AUTH_ENABLED toggle).
 *
 * Two-phase, non-blocking init:
 *  Phase 1 (sync-ish): Read token from localStorage — no network, no delay.
 *                       If valid, set authState immediately so the app renders authed.
 *                       If expired/missing, leave as UNAUTHED_STATE — landing page shows.
 *  Phase 2 (async):    Register UserManager events for background silent-renew failures.
 *
 * Auth redirect policy:
 *  - Token expiry / silent renew failure → clear auth state.
 *  - If on a PROTECTED route → redirect to Authentik (correct: they need to re-login).
 *  - If on landing page or /auth/* → do NOT redirect. Let the user click "Sign In" themselves.
 *    (prevents the app forcibly opening Authentik on startup if the token just expired)
 */

import { browser } from '$app/environment';
import {
  userManager,
  oidcUserToState,
  setAuthState,
  UNAUTHED_STATE,
  onSessionExpired,
} from '$lib/auth';

const PUBLIC_PATHS = ['/', '/auth/callback'];

function isPublicPath(path: string): boolean {
  if (path === '/') return true;
  return PUBLIC_PATHS.filter(p => p !== '/').some(p => path.startsWith(p));
}

function redirectToLoginIfProtected() {
  if (!browser || !userManager) return;
  const path = window.location.pathname;
  if (!isPublicPath(path)) {
    userManager.signinRedirect();
  }
  // On public routes (landing page): just clear auth state — user can click Sign In.
}

export async function init() {
  if (!userManager) return;

  // Phase 1: instant read from localStorage — no network round-trip
  try {
    const user = await userManager.getUser();
    if (user && !user.expired) {
      setAuthState(oidcUserToState(user));
    }
  } catch {
    // localStorage unavailable or corrupt — stay UNAUTHED_STATE
  }

  // Phase 2: register events (non-blocking — no await)
  registerUserManagerEvents();
}

function registerUserManagerEvents() {
  if (!userManager) return;

  // Background silent-renew failure → clear state, redirect only if on a protected route
  userManager.events.addSilentRenewError(() => {
    setAuthState(UNAUTHED_STATE);
    redirectToLoginIfProtected();
  });

  // Access token expired → try one explicit refresh, then redirect only if protected
  userManager.events.addAccessTokenExpired(() => {
    userManager!.signinSilent().catch(() => {
      setAuthState(UNAUTHED_STATE);
      redirectToLoginIfProtected();
    });
  });

  // Central handler for apiFetch's unrecoverable 401
  onSessionExpired(() => {
    setAuthState(UNAUTHED_STATE);
    redirectToLoginIfProtected();
  });
}
