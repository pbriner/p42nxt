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
  // Theme names are translated like everything modules define.
  await page.getByRole('option', { name: 'Blau' }).click();
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

test('Telegram: connected, the whole message or not, and what goes there', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'desktop', 'changes Pat’s choices: once is enough');
  await page.goto('/profile');
  await expect(page.getByText('Connected as pat_test')).toBeVisible();
  const whole = page.getByRole('switch', { name: 'Send the whole message' });
  await whole.click();
  await expect(page.getByText('Saved')).toBeVisible();
  await expect(whole).toBeChecked();
  const failed = page.getByRole('switch', { name: 'A scheduled job fails on Telegram' });
  await expect(failed).toBeChecked(); // the kind's default
  await failed.click();
  await page.reload();
  await expect(
    page.getByRole('switch', { name: 'A scheduled job fails on Telegram' }),
  ).not.toBeChecked();
  await expect(page.getByRole('switch', { name: 'Send the whole message' })).toBeChecked();
});

test.describe('someone without Telegram', () => {
  test.use({ storageState: storage('admin') });

  test('connects with a one-time link, and can cancel', async ({ page }) => {
    await page.goto('/profile');
    await page.getByRole('button', { name: 'Connect Telegram' }).click();
    const open = page.getByRole('link', { name: 'Open Telegram' });
    await expect(open).toHaveAttribute('href', /^https:\/\/t\.me\/p42_test_bot\?start=[\w-]{22,}$/);
    await expect(page.getByText('The link works for 15 minutes.')).toBeVisible();
    await noSidewaysScroll(page);
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('button', { name: 'Connect Telegram' })).toBeVisible();
  });
});
