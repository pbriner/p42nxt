import { expect, test } from '@playwright/test';

import { choose, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('an exchange rate is entered with currencies picked from those offered', async ({
  page,
}, info) => {
  // A day of its own per browser size: a currency has one rate per day and base.
  const day = `2025-0${(info.workerIndex % 9) + 1}-1${info.project.name.length % 10}`;
  await page.goto('/form/sys.exchange_rate/new');
  await page.getByLabel('Date').fill(day);
  await choose(
    page,
    page.getByRole('combobox', { name: 'Currency', exact: true }),
    'GBP · Pound sterling',
  );
  await choose(
    page,
    page.getByRole('combobox', { name: 'Base currency', exact: true }),
    'CHF · Swiss franc',
  );
  await page.getByRole('spinbutton', { name: 'Rate' }).fill('1.1325');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByText('Exchange rate created')).toBeVisible();
  await noSidewaysScroll(page);
});
