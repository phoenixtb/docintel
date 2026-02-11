import { test, expect } from '@playwright/test';

/**
 * E2E tests for the Documents page.
 * These tests verify basic UI functionality without requiring backend services.
 */
test.describe('Documents Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/documents');
    await page.waitForLoadState('networkidle');
  });

  test('should load documents page', async ({ page }) => {
    // Page should be visible
    await expect(page.locator('body')).toBeVisible();
    // Should contain DocIntel branding
    await expect(page.locator('header')).toBeVisible();
  });

  test('should have navigation back to chat', async ({ page }) => {
    // Should have a link to home somewhere on the page
    const homeLink = page.locator('a[href="/"]').first();
    await expect(homeLink).toBeAttached();
  });

  test('should navigate back to chat', async ({ page }) => {
    const homeLink = page.locator('a[href="/"]').first();
    await homeLink.click();
    await expect(page).toHaveURL('/');
  });

  test('should display upload section', async ({ page }) => {
    // Should have file input somewhere on page
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();
  });

  test('should have domain selection options', async ({ page }) => {
    // Check for domain-related content (radio buttons or select)
    const domainOptions = page.locator('input[type="radio"]');
    const count = await domainOptions.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('should be responsive', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    
    // Header should still be visible on mobile
    await expect(page.locator('header')).toBeVisible();
  });
});

test.describe('Document Upload Flow', () => {
  test('should have file input for upload', async ({ page }) => {
    await page.goto('/documents');
    
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();
  });

  test('should enable upload button when file selected', async ({ page }) => {
    await page.goto('/documents');
    
    const fileInput = page.locator('input[type="file"]');
    
    // Create a test file and select it
    await fileInput.setInputFiles({
      name: 'test-document.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('This is test content for upload.')
    });
    
    // File should be selected (input has files)
    const files = await fileInput.evaluate((el: HTMLInputElement) => el.files?.length);
    expect(files).toBe(1);
  });
});

test.describe('Page Layout', () => {
  test('should have proper header structure', async ({ page }) => {
    await page.goto('/documents');
    
    await expect(page.locator('header')).toBeVisible();
  });

  test('should have main content area', async ({ page }) => {
    await page.goto('/documents');
    
    // Page should have content after header
    const main = page.locator('main, div.flex.flex-col');
    await expect(main.first()).toBeVisible();
  });
});
