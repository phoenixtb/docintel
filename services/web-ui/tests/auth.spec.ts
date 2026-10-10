import { APP_ORIGIN, CLIENT_ID, IDP_ORIGIN } from './support/fakeIdp';
import { expect, test, userWithRole } from './support/fixtures';

test.describe('Signed out', () => {
  test('shows the landing page with a sign-in button', async ({ page, idp }) => {
    void idp;
    await page.goto('/');

    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
  });

  test('sends a visitor to a protected page to the IdP with a PKCE authorization request', async ({ page, idp }) => {
    idp.autoApprove = false;

    await page.goto('/documents');
    await page.waitForURL(`${IDP_ORIGIN}/oauth/v2/authorize**`);

    const params = idp.authorizeRequests[0].url.searchParams;
    expect(params.get('response_type')).toBe('code');
    expect(params.get('client_id')).toBe(CLIENT_ID);
    expect(params.get('redirect_uri')).toBe(`${APP_ORIGIN}/auth/callback`);
    expect(params.get('code_challenge_method')).toBe('S256');
    expect(params.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(params.get('scope')?.split(' ')).toEqual(
      expect.arrayContaining(['openid', 'profile', 'email', 'offline_access']),
    );
    expect(params.get('state')).toBeTruthy();
  });

  test('starts the login flow from the Sign In button', async ({ page, idp }) => {
    idp.autoApprove = false;
    await page.goto('/');

    await page.getByRole('button', { name: 'Sign In' }).click();

    await page.waitForURL(`${IDP_ORIGIN}/oauth/v2/authorize**`);
    expect(idp.authorizeRequests).toHaveLength(1);
  });
});

test.describe('Signing in', () => {
  test('returns the user to the page they asked for, signed in', async ({ page, idp, signIn }) => {
    await signIn('/documents');

    await expect(page).toHaveURL('/documents');
    await expect(page.getByRole('heading', { name: 'Documents', level: 1 })).toBeVisible();
    // The code exchange carried the PKCE verifier the IdP checked against the challenge.
    expect(idp.tokenRequests).toHaveLength(1);
    expect(idp.tokenRequests[0].get('code_verifier')).toBeTruthy();
  });

  test('sends the access token and tenant on API calls', async ({ api, signIn }) => {
    await signIn('/documents');

    await expect.poll(() => api.requestsTo('GET', '/api/v1/documents').length).toBeGreaterThan(0);
    const call = api.requestsTo('GET', '/api/v1/documents')[0];
    expect(call.headers['authorization']).toBe('Bearer access-user-1');
    expect(call.headers['x-tenant-id']).toBe('acme');
  });

  test('keeps the session across a reload without going back to the IdP', async ({ page, idp, signIn }) => {
    await signIn('/chat');

    await page.reload();

    await expect(page.getByRole('banner')).toBeVisible();
    expect(idp.authorizeRequests).toHaveLength(1);
  });

  test('signs the user out back to the landing page', async ({ page, signIn }) => {
    await signIn('/chat');

    await page.getByRole('button', { name: 'Account menu' }).click();
    await page.getByRole('button', { name: /sign out/i }).click();

    await expect(page).toHaveURL('/');
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
  });
});

test.describe('Role guards', () => {
  test('shows a tenant user only Chat and Documents in the navigation', async ({ page, signIn }) => {
    await signIn('/chat');

    const nav = page.getByRole('navigation');
    await expect(nav.getByRole('link')).toHaveText(['Chat', 'Documents']);
  });

  test('redirects a tenant user away from the platform admin page', async ({ page, signIn }) => {
    await signIn('/chat');

    await page.goto('/admin');

    await expect(page).toHaveURL('/chat');
  });

  test.describe('as a platform admin', () => {
    test.use({ user: userWithRole('platform_admin') });

    test('shows the Manage, Insights and Platform sections', async ({ page, signIn }) => {
      await signIn('/chat');

      await expect(page.getByRole('navigation').getByRole('link')).toHaveText([
        'Chat',
        'Documents',
        'Manage',
        'Insights',
        'Platform',
      ]);
    });
  });
});

test('shows the error page for an unknown route', async ({ page, signIn }) => {
  await signIn('/chat');

  await page.goto('/no-such-page');

  await expect(page.getByText('404')).toBeVisible();
});
