// Runtime environment configuration — injected by docker-entrypoint.sh at container start.
// Placeholders (__PUBLIC_*__) are replaced with actual env var values via sed.
// This file is served with no-cache headers so values always reflect the current container env.
window.__DOCINTEL_ENV__ = {
  PUBLIC_API_URL: '__PUBLIC_API_URL__',
  PUBLIC_ZITADEL_ISSUER: '__PUBLIC_ZITADEL_ISSUER__',
  PUBLIC_ZITADEL_CLIENT_ID: '__PUBLIC_ZITADEL_CLIENT_ID__'
};
