import { expect, test } from '@playwright/test';

import { api, noSidewaysScroll, opened, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a year is spread, and the version is submitted, approved and locked', async ({
  page,
}, info) => {
  const tag = `${info.project.name.slice(0, 3)}${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  const house = (
    await client.post('/api/v1/costing/center', { code: `BH${tag}`, name: 'House', kind: 'profit' })
  ).sys_id;
  await client.post('/api/v1/costing/center', { code: `BG${tag}`, name: 'Garden', parent: house });
  await client.post('/api/v1/costing/cost_type', { code: `BC${tag}`, name: 'Gardening' });
  const version = (
    await client.post('/api/v1/budget/version', { name: `Budget ${tag}`, year: 2027 })
  ).sys_id;

  await page.goto(`/form/budget.version/${version}`);
  await page.getByRole('button', { name: 'Spread a year' }).click();
  const dialog = await opened(page);
  await dialog.getByLabel('Cost center (code)').fill(`BG${tag}`);
  await dialog.getByLabel('Cost type (code)').fill(`BC${tag}`);
  await dialog.getByLabel('Amount for the year').fill('1200');
  await dialog.getByRole('button', { name: 'Spread a year' }).click();
  await expect(page.getByText('1,200.00 spread over the year: 100.00 a month.')).toBeVisible();

  for (const [move, next] of [
    ['Submit', 'Approve'],
    ['Approve', 'Lock'],
  ]) {
    await page.getByRole('button', { name: move }).click();
    await expect(page.getByRole('button', { name: next })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Lock' }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /Lock|Confirm|OK|Yes/ })
    .click();
  await expect(page.getByRole('button', { name: 'Spread a year' })).toHaveCount(0);
  await noSidewaysScroll(page);
});
