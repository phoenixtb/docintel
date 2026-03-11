/**
 * Runtime environment configuration for the static SPA.
 *
 * Values are injected at container startup by docker-entrypoint.sh into
 * /env-config.js, which sets window.__DOCINTEL_ENV__. This file provides
 * a typed accessor with sensible defaults for local dev (without Docker).
 */

interface RuntimeEnv {
  PUBLIC_API_URL: string;
  PUBLIC_ZITADEL_ISSUER: string;
  PUBLIC_ZITADEL_CLIENT_ID: string;
}

const DEFAULTS: RuntimeEnv = {
  PUBLIC_API_URL: 'http://localhost:8080',
  PUBLIC_ZITADEL_ISSUER: 'http://localhost:9090',
  PUBLIC_ZITADEL_CLIENT_ID: '',
};

export function getEnv(): RuntimeEnv {
  if (typeof window === 'undefined') return DEFAULTS;
  return { ...DEFAULTS, ...((window as unknown as { __DOCINTEL_ENV__?: Partial<RuntimeEnv> }).__DOCINTEL_ENV__ || {}) };
}
