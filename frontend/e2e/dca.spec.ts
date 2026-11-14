import { expect, test } from '@playwright/test';

import { noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('plain') });

test('a DCA plan is made in the form and says when it buys', async ({ page }, info) => {
  const name = `Bitcoin ${info.project.name}`;
  await page.goto('/form/dca.plan/new');
  await page.getByRole('textbox', { name: 'Name' }).fill(name);
  await page.getByRole('spinbutton', { name: 'Amount per buy' }).fill('0.0001');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(
    page.getByText('The next buy is planned at the next check (within 5 minutes).'),
  ).toBeVisible();
  await noSidewaysScroll(page);

  await page.getByRole('spinbutton', { name: 'Deposit day (1 to 28)' }).fill('30');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('mat-hint.field-error', { hasText: 'From 1 to 28.' })).toBeVisible();
});
