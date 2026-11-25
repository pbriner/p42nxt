import { expect, test } from '@playwright/test';

import { api, choose, compact, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a list adds up its amounts, shows fields of referenced records and exports', async ({
  page,
}, info) => {
  const tag = `T${Date.now() % 1000000}${info.project.name.slice(0, 2)}`;
  await page.goto('/');
  const client = await api(page);
  const partner = (
    await client.post('/api/v1/partners/partner', { name: `Baker ${tag}`, city: 'Olten' })
  ).sys_id;
  const center = (
    await client.post('/api/v1/costing/center', { code: `C-${tag}`, name: `Shop ${tag}` })
  ).sys_id;
  const kind = (
    await client.post('/api/v1/costing/cost_type', { code: `K-${tag}`, name: `Bread ${tag}` })
  ).sys_id;
  for (const amount of ['120.50', '79.50']) {
    await client.post('/api/v1/finance/booking', {
      date: '2026-04-01',
      text: `Bread ${tag}`,
      partner,
      cost_type: kind,
      center,
      amount,
    });
  }
  await page.goto(`/list/finance.booking?q=${tag}`);
  if (!compact(info)) {
    // The whole filter's total, per currency, under the amounts.
    const footer = page.locator('tfoot');
    await expect(footer).toContainText('Total');
    await expect(footer).toContainText('CHF 200.00');

    await page.getByRole('button', { name: 'Columns' }).click();
    const dialog = page.getByRole('dialog');
    await choose(page, dialog.getByRole('combobox', { name: 'Add a field of' }), 'Partner');
    const field = dialog.getByRole('combobox', { name: 'Field', exact: true });
    await expect(field).not.toHaveAttribute('aria-disabled', 'true'); // its fields are loaded
    await choose(page, field, 'City');
    await dialog.getByRole('button', { name: 'Add', exact: true }).click();
    await dialog.getByRole('button', { name: 'Apply' }).click();
    await expect(page.getByRole('columnheader', { name: 'Partner › City' })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'Olten' }).first()).toBeVisible();
  }

  await page.getByRole('button', { name: 'Export' }).click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('menuitem', { name: 'CSV' }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^finance-booking-.*\.csv$/);
  const text = await (await download.createReadStream()).toArray();
  const csv = Buffer.concat(text).toString('utf-8');
  expect(csv).toContain(`Bread ${tag}`);
  expect(csv.split('\n').filter((l) => l.includes(tag))).toHaveLength(2);
  await noSidewaysScroll(page);
});
