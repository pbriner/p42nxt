import { expect, test } from '@playwright/test';

import { api, choose, noSidewaysScroll, opened, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a line with a rhythm fills its year, and the version is approved and locked', async ({
  page,
}, info) => {
  const tag = `${info.project.name.slice(0, 3)}${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  const house = (
    await client.post('/api/v1/costing/center', { code: `BH${tag}`, name: 'House', kind: 'profit' })
  ).sys_id;
  await client.post('/api/v1/costing/center', {
    code: `BG${tag}`,
    name: `Garden ${tag}`,
    parent: house,
  });
  await client.post('/api/v1/costing/cost_type', { code: `BC${tag}`, name: `Gardening ${tag}` });
  const version = (
    await client.post('/api/v1/budget/version', { name: `Budget ${tag}`, year: 2027 })
  ).sys_id;

  await page.goto(`/form/budget.version/${version}`);
  await page.getByRole('tab', { name: /Budget lines/ }).click();
  await page.getByRole('link', { name: 'New' }).click();
  const dialog = await opened(page);
  const center = dialog.getByRole('combobox', { name: 'Cost center' });
  await center.fill(`Garden ${tag}`);
  await page.getByRole('option', { name: new RegExp(`Garden ${tag}`) }).click();
  const costType = dialog.getByRole('combobox', { name: 'Cost type' });
  await costType.fill(`Gardening ${tag}`);
  await page.getByRole('option', { name: new RegExp(`Gardening ${tag}`) }).click();
  await choose(page, dialog.getByRole('combobox', { name: 'Rhythm' }), 'Quarterly');
  await dialog.getByRole('spinbutton', { name: 'Amount', exact: true }).fill('500');
  await dialog.getByRole('button', { name: 'Create' }).click();
  await expect(dialog).toBeHidden();
  // Four quarters: the year total is worked out.
  await expect(page.getByText('2,000.00').first()).toBeVisible();

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
  await expect(page.getByRole('button', { name: 'Lock' })).toHaveCount(0);
  await noSidewaysScroll(page);
});
