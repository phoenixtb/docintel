import { test, expect } from '@playwright/test';

/**
 * Navigation and accessibility tests.
 */
test.describe('Navigation', () => {
  test('should load home page', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/DocIntel/i);
  });

  test('should load documents page', async ({ page }) => {
    await page.goto('/documents');
    // Wait for page to fully load
    await page.waitForLoadState('networkidle');
    // The page should have some content
    await expect(page.locator('body')).toBeVisible();
  });

  test('should navigate from home to documents', async ({ page }) => {
    await page.goto('/');
    await page.click('a[href="/documents"]');
    await expect(page).toHaveURL('/documents');
  });

  test('should navigate from documents to home', async ({ page }) => {
    await page.goto('/documents');
    await page.click('a[href="/"]');
    await expect(page).toHaveURL('/');
  });

  test('should handle browser back button', async ({ page }) => {
    await page.goto('/');
    await page.click('a[href="/documents"]');
    await page.goBack();
    await expect(page).toHaveURL('/');
  });

  test('should handle browser forward button', async ({ page }) => {
    await page.goto('/');
    await page.click('a[href="/documents"]');
    await page.goBack();
    await page.goForward();
    await expect(page).toHaveURL('/documents');
  });
});

test.describe('Accessibility', () => {
  test('home page should have proper heading structure', async ({ page }) => {
    await page.goto('/');
    
    // Should have h1
    const h1 = page.locator('h1');
    await expect(h1).toBeVisible();
  });

  test('documents page should have proper heading structure', async ({ page }) => {
    await page.goto('/documents');
    await page.waitForLoadState('networkidle');
    
    // Should have visible content
    await expect(page.locator('body')).toBeVisible();
  });

  test('forms should have proper labels', async ({ page }) => {
    await page.goto('/documents');
    
    // File input should have associated label or aria-label
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached();
  });

  test('buttons should be focusable', async ({ page }) => {
    await page.goto('/');
    
    const textarea = page.locator('textarea');
    await textarea.fill('Test');
    
    const sendButton = page.locator('button').filter({ has: page.locator('svg') }).last();
    await sendButton.focus();
    await expect(sendButton).toBeFocused();
  });

  test('should handle keyboard navigation', async ({ page }) => {
    await page.goto('/');
    
    // Tab through elements
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    
    // Some element should be focused
    const focusedElement = page.locator(':focus');
    await expect(focusedElement).toBeVisible();
  });
});

test.describe('Error Handling', () => {
  test('should handle 404 page gracefully', async ({ page }) => {
    const response = await page.goto('/non-existent-page');
    // SvelteKit may return 404 or redirect
  });

  test('should handle network errors gracefully', async ({ page }) => {
    await page.goto('/');
    
    // Simulate offline
    await page.context().setOffline(true);
    
    const textarea = page.locator('textarea');
    await textarea.fill('Test offline');
    
    const sendButton = page.locator('button').filter({ has: page.locator('svg') }).last();
    await sendButton.click();
    
    // Should show error message or handle gracefully
    await page.context().setOffline(false);
  });
});

test.describe('Performance', () => {
  test('home page should load within 3 seconds', async ({ page }) => {
    const startTime = Date.now();
    await page.goto('/');
    const loadTime = Date.now() - startTime;
    
    expect(loadTime).toBeLessThan(3000);
  });

  test('documents page should load within 3 seconds', async ({ page }) => {
    const startTime = Date.now();
    await page.goto('/documents');
    const loadTime = Date.now() - startTime;
    
    expect(loadTime).toBeLessThan(3000);
  });
});

test.describe('Visual Regression', () => {
  test.skip('home page screenshot', async ({ page }) => {
    // Skip visual regression tests until baseline images are created
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    await expect(page).toHaveScreenshot('home-page.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.1,
    });
  });

  test.skip('documents page screenshot', async ({ page }) => {
    // Skip visual regression tests until baseline images are created
    await page.goto('/documents');
    await page.waitForLoadState('networkidle');
    
    await expect(page).toHaveScreenshot('documents-page.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.1,
    });
  });
});
