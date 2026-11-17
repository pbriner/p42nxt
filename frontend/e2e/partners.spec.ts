import { expect, test } from '@playwright/test';

import { noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a supplier is kept with a checked IBAN', async ({ page }, info) => {
  const name = `Roofer ${info.project.name} ${Date.now() % 100000}`;
  await page.goto('/form/partners.partner/new');
  await page.getByRole('textbox', { name: 'Name' }).fill(name);
  await page.getByRole('switch', { name: 'Supplier' }).click();
  await page.getByRole('textbox', { name: 'IBAN' }).fill('ch93 0076 2011 6238 5295 8');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(
    page.locator('mat-hint.field-error', { hasText: 'Not a valid IBAN: check the digits.' }),
  ).toBeVisible();

  await page.getByRole('textbox', { name: 'IBAN' }).fill('ch9300762011623852957');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByText('Partner created')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'IBAN' })).toHaveValue(
    'CH93 0076 2011 6238 5295 7',
  );
  await noSidewaysScroll(page);
});
