import { expect, test } from '@playwright/test';

import { noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('plain') });

test('mode, language and theme apply at once and stay', async ({ page }) => {
  await page.goto('/profile');
  await noSidewaysScroll(page);
  const html = page.locator('html');

  await page.getByRole('radio', { name: 'Dark' }).click();
  await expect(html).toHaveClass(/theme-dark/);

  await page.getByRole('combobox', { name: 'Language' }).click();
  await page.getByRole('option', { name: 'Deutsch' }).click();
  await expect(page.getByRole('heading', { name: 'Profil und Einstellungen' })).toBeVisible();
  await expect(html).toHaveAttribute('lang', 'de');

  await page.getByRole('combobox', { name: 'Farbschema' }).click();
  await page.getByRole('option', { name: 'Blue' }).click();
  await expect.poll(() => html.getAttribute('data-cards')).toBe('outlined');

  await page.reload();
  await expect(html).toHaveClass(/theme-dark/);
  await expect(page.getByRole('heading', { name: 'Profil und Einstellungen' })).toBeVisible();

  // Back as it was, for the other sizes.
  await page.getByRole('radio', { name: 'System' }).click();
  await page.getByRole('combobox', { name: 'Sprache' }).click();
  await page.getByRole('option', { name: 'Wie der Browser' }).click();
  await page.getByRole('combobox', { name: 'Colour theme' }).click();
  await page.getByRole('option', { name: /Violet \(default\)/ }).click();
  await expect(html).toHaveAttribute('data-cards', 'raised');
  await expect(html).not.toHaveClass(/theme-dark/);
});
