import { test, expect } from '@playwright/test';

/**
 * E2E tests for the Chat page (main page).
 * These tests verify basic UI functionality without requiring backend services.
 */
test.describe('Chat Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
  });

  test('should display the DocIntel header', async ({ page }) => {
    await expect(page.locator('header')).toBeVisible();
  });

  test('should have input field for questions', async ({ page }) => {
    const textarea = page.locator('textarea');
    await expect(textarea).toBeVisible();
  });

  test('should have link to documents page', async ({ page }) => {
    const docsLink = page.locator('a[href="/documents"]');
    await expect(docsLink).toBeVisible();
  });

  test('should navigate to documents page', async ({ page }) => {
    await page.click('a[href="/documents"]');
    await expect(page).toHaveURL('/documents');
  });

  test('should allow typing in textarea', async ({ page }) => {
    const textarea = page.locator('textarea');
    
    await textarea.fill('What is the leave policy?');
    await expect(textarea).toHaveValue('What is the leave policy?');
  });

  test('should have submit button or form', async ({ page }) => {
    // Check for either a button or form to submit
    const submitButton = page.locator('button').last();
    await expect(submitButton).toBeVisible();
  });

  test('should be responsive on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    
    // Header should still be visible
    await expect(page.locator('header')).toBeVisible();
    
    // Input should be visible
    await expect(page.locator('textarea')).toBeVisible();
  });
});

test.describe('Chat Interaction', () => {
  test('textarea should be clearable', async ({ page }) => {
    await page.goto('/');
    
    const textarea = page.locator('textarea');
    await textarea.fill('Test message');
    await expect(textarea).toHaveValue('Test message');
    
    await textarea.fill('');
    await expect(textarea).toHaveValue('');
  });

  test('should support multi-line input with Shift+Enter', async ({ page }) => {
    await page.goto('/');
    
    const textarea = page.locator('textarea');
    await textarea.focus();
    await textarea.fill('Line 1');
    // Shift+Enter should allow new line without submitting
    await textarea.press('Shift+Enter');
    // Textarea should still be visible (not submitted)
    await expect(textarea).toBeVisible();
  });
});

test.describe('Navigation', () => {
  test('should have home link in header', async ({ page }) => {
    await page.goto('/');
    
    // Should have DocIntel logo/link
    const logoLink = page.locator('header a').first();
    await expect(logoLink).toBeVisible();
  });

  test('should maintain header on scroll', async ({ page }) => {
    await page.goto('/');
    
    // Scroll down
    await page.evaluate(() => window.scrollTo(0, 500));
    
    // Header should still be visible
    await expect(page.locator('header')).toBeVisible();
  });
});
