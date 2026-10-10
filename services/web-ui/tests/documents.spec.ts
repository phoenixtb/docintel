import { storedDocument } from './support/fakeApi';
import { expect, expectNoA11yViolations, test } from './support/fixtures';

const HANDBOOK = new URL('./fixtures/handbook.txt', import.meta.url).pathname;

test.describe('Documents', () => {
  test('lists the tenant\'s uploaded documents with their status', async ({ page, api, signIn }) => {
    api.documents = [
      storedDocument({ id: 'doc-1', filename: 'leave-policy.pdf', status: 'COMPLETED', metadata: { source: 'upload' } }),
      storedDocument({ id: 'doc-2', filename: 'broken-scan.pdf', status: 'FAILED', metadata: { source: 'upload' } }),
    ];
    await signIn('/documents');

    const completed = page.getByRole('row', { name: /leave-policy\.pdf/ });
    await expect(completed).toBeVisible();
    await expect(completed).toContainText('COMPLETED');
    const failed = page.getByRole('row', { name: /broken-scan\.pdf/ });
    await expect(failed).toBeVisible();
    await expect(failed).toContainText('FAILED');
  });

  test('shows an empty state when the tenant has no documents', async ({ page, signIn }) => {
    await signIn('/documents');

    await expect(page.getByText('No uploaded documents yet.')).toBeVisible();
    await expect(page.getByText('Upload a document above to get started.')).toBeVisible();
  });

  test('keeps the Upload button disabled until a file is chosen', async ({ page, signIn }) => {
    await signIn('/documents');
    const upload = page.getByRole('button', { name: 'Upload', exact: true });
    await expect(upload).toBeDisabled();

    await page.getByLabel('Document file to upload').setInputFiles(HANDBOOK);

    await expect(upload).toBeEnabled();
  });

  test('uploads the chosen file with the selected domain', async ({ page, api, signIn }) => {
    await signIn('/documents');

    await page.getByRole('radio', { name: 'HR Policy' }).check();
    await page.getByLabel('Document file to upload').setInputFiles(HANDBOOK);
    await page.getByRole('button', { name: 'Upload', exact: true }).click();

    await expect(page.getByText('Uploaded "handbook.txt" successfully')).toBeVisible();
    const uploads = api.requestsTo('POST', '/api/v1/documents');
    expect(uploads).toHaveLength(1);
    expect(uploads[0].body).toContain('filename="handbook.txt"');
    expect(uploads[0].body).toMatch(/name="domain"\r\n\r\nhr_policy\r\n/);
  });

  test('shows an error when the upload fails', async ({ page, api, signIn }) => {
    api.on('POST', '/api/v1/documents', () => ({ status: 500, json: { error: { code: 'INTERNAL' } } }));
    await signIn('/documents');

    await page.getByLabel('Document file to upload').setInputFiles(HANDBOOK);
    await page.getByRole('button', { name: 'Upload', exact: true }).click();

    await expect(page.getByText('Upload failed: HTTP 500')).toBeVisible();
    await expect(page.getByText('Uploaded "handbook.txt" successfully')).toHaveCount(0);
  });

  test('shows the indexed vector count from vector-stats', async ({ page, signIn }) => {
    await signIn('/documents');

    await expect(page.getByText('Total Chunks')).toBeVisible();
    await expect(page.getByText('HR Policy', { exact: true }).first()).toBeVisible();
    // total_vectors and the hr_policy tenant stat are both 120
    await expect(page.getByText('120', { exact: true })).toHaveCount(2);
    await expect(page.getByText('No documents indexed yet.')).toHaveCount(0);
  });

  test('has no WCAG 2.1 AA violations on the documents page', async ({ page, api, signIn }) => {
    api.documents = [storedDocument({ filename: 'leave-policy.pdf', metadata: { source: 'upload' } })];
    await signIn('/documents');
    await expect(page.getByRole('row', { name: /leave-policy\.pdf/ })).toBeVisible();
    await expect(page.getByText('Total Chunks')).toBeVisible();

    await expectNoA11yViolations(page);
  });
});

test.describe('Landing page', () => {
  test('has no WCAG 2.1 AA violations when signed out', async ({ page, idp }) => {
    void idp;
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();

    await expectNoA11yViolations(page);
  });
});
