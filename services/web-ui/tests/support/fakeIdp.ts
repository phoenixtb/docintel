/**
 * A stand-in OIDC provider served through Playwright request routing.
 *
 * The SPA runs its real oidc-client-ts code path: discovery, a PKCE
 * authorization redirect, the /auth/callback code exchange and userinfo.
 * The IdP verifies the PKCE pair (S256(code_verifier) === code_challenge),
 * so a broken PKCE implementation fails the login rather than passing silently.
 */
import type { Page, Request } from '@playwright/test';

export const APP_ORIGIN = 'http://localhost:4173';
export const IDP_ORIGIN = 'http://idp.test';
export const API_ORIGIN = 'http://api.test';
export const CLIENT_ID = 'e2e-client';

export type Role = 'tenant_user' | 'tenant_admin' | 'platform_admin';

export interface FakeUser {
  sub: string;
  email: string;
  name: string;
  tenantId: string;
  role: Role;
}

export const DEFAULT_USER: FakeUser = {
  sub: 'user-1',
  email: 'ana@acme.test',
  name: 'Ana Analyst',
  tenantId: 'acme',
  role: 'tenant_user',
};

export const CORS_HEADERS = {
  'access-control-allow-origin': APP_ORIGIN,
  'access-control-allow-headers': '*',
  'access-control-allow-methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
};

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const utf8 = (text: string) => new TextEncoder().encode(text);

async function s256(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', utf8(verifier))));
}

/** An unsigned JWT: oidc-client-ts decodes the id_token's claims but never verifies its signature. */
function unsignedJwt(claims: Record<string, unknown>): string {
  return `${base64url(utf8(JSON.stringify({ alg: 'none', typ: 'JWT' })))}.${base64url(utf8(JSON.stringify(claims)))}.sig`;
}

export interface AuthorizeRequest {
  url: URL;
}

export class FakeIdp {
  /** Every /authorize request the browser made, in order. */
  readonly authorizeRequests: AuthorizeRequest[] = [];
  /** Form bodies of every token-endpoint request. */
  readonly tokenRequests: URLSearchParams[] = [];
  /** When false, /authorize renders a login page instead of redirecting back with a code. */
  autoApprove = true;

  private readonly challenges = new Map<string, string>(); // code -> code_challenge

  constructor(private readonly user: FakeUser = DEFAULT_USER) {}

  async install(page: Page): Promise<void> {
    // The SPA reads its runtime config from /env-config.js (filled in by the
    // container entrypoint in production).
    await page.route(`${APP_ORIGIN}/env-config.js`, (route) =>
      route.fulfill({
        contentType: 'application/javascript',
        body: `window.__DOCINTEL_ENV__ = ${JSON.stringify({
          PUBLIC_API_URL: API_ORIGIN,
          PUBLIC_ZITADEL_ISSUER: IDP_ORIGIN,
          PUBLIC_ZITADEL_CLIENT_ID: CLIENT_ID,
        })};`,
      }),
    );

    await page.route(`${IDP_ORIGIN}/**`, async (route, request) => {
      const url = new URL(request.url());
      if (request.method() === 'OPTIONS') {
        return route.fulfill({ status: 204, headers: CORS_HEADERS });
      }
      switch (url.pathname) {
        case '/.well-known/openid-configuration':
          return route.fulfill({ headers: CORS_HEADERS, json: this.discovery() });
        case '/oauth/v2/authorize':
          return this.authorize(route, url);
        case '/oauth/v2/token':
          return route.fulfill({ headers: CORS_HEADERS, ...(await this.token(request)) });
        case '/oidc/v1/userinfo':
          return route.fulfill({ headers: CORS_HEADERS, json: this.userinfo() });
        case '/oidc/v1/end_session': {
          const back = url.searchParams.get('post_logout_redirect_uri') ?? `${APP_ORIGIN}/`;
          return route.fulfill({ status: 302, headers: { location: back } });
        }
        default:
          return route.fulfill({ status: 404, headers: CORS_HEADERS, body: 'unknown IdP path' });
      }
    });
  }

  private discovery() {
    return {
      issuer: IDP_ORIGIN,
      authorization_endpoint: `${IDP_ORIGIN}/oauth/v2/authorize`,
      token_endpoint: `${IDP_ORIGIN}/oauth/v2/token`,
      userinfo_endpoint: `${IDP_ORIGIN}/oidc/v1/userinfo`,
      end_session_endpoint: `${IDP_ORIGIN}/oidc/v1/end_session`,
      jwks_uri: `${IDP_ORIGIN}/oauth/v2/keys`,
      response_types_supported: ['code'],
      code_challenge_methods_supported: ['S256'],
    };
  }

  private authorize(route: Parameters<Parameters<Page['route']>[1]>[0], url: URL) {
    this.authorizeRequests.push({ url });
    if (!this.autoApprove) {
      return route.fulfill({ contentType: 'text/html', body: '<title>IdP login</title><h1>IdP login</h1>' });
    }
    const code = `code-${this.authorizeRequests.length}`;
    this.challenges.set(code, url.searchParams.get('code_challenge') ?? '');
    const back = new URL(url.searchParams.get('redirect_uri') ?? `${APP_ORIGIN}/auth/callback`);
    back.searchParams.set('code', code);
    back.searchParams.set('state', url.searchParams.get('state') ?? '');
    return route.fulfill({ status: 302, headers: { location: back.toString() } });
  }

  private async token(request: Request): Promise<{ status: number; json: unknown }> {
    const form = new URLSearchParams(request.postData() ?? '');
    this.tokenRequests.push(form);
    const challenge = this.challenges.get(form.get('code') ?? '');
    const verifier = form.get('code_verifier') ?? '';
    const expected = await s256(verifier);
    if (form.get('grant_type') !== 'authorization_code' || !challenge || challenge !== expected) {
      return { status: 400, json: { error: 'invalid_grant' } };
    }
    const now = Math.floor(Date.now() / 1000);
    return {
      status: 200,
      json: {
        access_token: `access-${this.user.sub}`,
        refresh_token: `refresh-${this.user.sub}`,
        token_type: 'Bearer',
        expires_in: 3600,
        scope: 'openid profile email offline_access',
        id_token: unsignedJwt({
          iss: IDP_ORIGIN,
          aud: CLIENT_ID,
          sub: this.user.sub,
          iat: now,
          exp: now + 3600,
        }),
      },
    };
  }

  private userinfo() {
    return {
      sub: this.user.sub,
      email: this.user.email,
      name: this.user.name,
      tenant_id: this.user.tenantId,
      role: this.user.role,
    };
  }
}
