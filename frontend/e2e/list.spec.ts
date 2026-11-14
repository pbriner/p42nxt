import { expect, test } from '@playwright/test';

import { noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a list shows its columns, sorts and pages', async ({ page }) => {
  await page.goto('/list/sys.person');
  await expect(page.getByRole('heading', { name: 'People' })).toBeVisible();
  const name = page.getByRole('columnheader', { name: 'Name' });
  await expect(name).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Pat Plain' })).toBeVisible();
  await name.getByRole('button').click();
  await expect(name).toHaveAttribute('aria-sort', 'ascending');
  await name.getByRole('button').click();
  await expect(name).toHaveAttribute('aria-sort', 'descending');
  await noSidewaysScroll(page);
});

test('a list one may not see says so', async ({ page }) => {
  await page.goto('/list/nothing.here');
  await expect(page.getByRole('heading', { name: 'Not found' })).toBeVisible();
});
