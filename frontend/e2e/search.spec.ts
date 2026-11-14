import { expect, test } from '@playwright/test';

import { compact, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('the search finds people and items, and shows everything on its page', async ({
  page,
}, info) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Hello, Ada Admin' })).toBeVisible();
  if (compact(info)) {
    await page.getByRole('link', { name: 'Search' }).click();
  } else {
    await page.keyboard.press('ControlOrMeta+k');
    const field = page.getByRole('combobox', { name: 'Search', exact: true });
    await expect(field).toBeFocused();
    await field.fill('Pat');
    const panel = page.getByRole('listbox');
    await expect(panel.getByText('People (1)')).toBeVisible();
    await expect(panel.getByText('Pat Plain', { exact: false })).toBeVisible();
    await field.press('Enter');
  }
  if (compact(info)) {
    await page.getByLabel('Search for').fill('Pat');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
  }
  await expect(page).toHaveURL(/\/search\?q=Pat/);
  await expect(page.getByText('result(s) for “Pat”', { exact: false })).toBeVisible();
  await expect(page.getByText('Pat Plain', { exact: false }).first()).toBeVisible();
  await noSidewaysScroll(page);

  await page.getByLabel('Search for').fill('p');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('Enter at least 2 characters.')).toBeVisible();
});
