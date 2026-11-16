import { expect, test } from '@playwright/test';

import { api, choose, noSidewaysScroll, opened, storage } from './helpers';

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

  // Small amounts keep their decimals in lists (not rounded to 0).
  await page.goto(`/list/dca.plan?q=${encodeURIComponent(name)}`);
  // A table row on wider screens, a card on phones.
  const entry = page
    .getByRole('row', { name: new RegExp(name) })
    .or(page.getByRole('listitem', { name }));
  await expect(entry).toContainText('0.0001');
});

test('the DCA dashboard shows one plan at a time', async ({ page }, info) => {
  await page.goto('/dashboard/dca');
  const choice = page.getByRole('combobox', { name: 'DCA plan' });
  await expect(choice).toBeVisible();
  await choice.click();
  await page.getByRole('option', { name: `Bitcoin ${info.project.name}` }).click();
  const invested = page.locator('app-widget-card').filter({ hasText: 'Invested' }).first();
  await expect(invested.getByText(`Bitcoin ${info.project.name} · dry runs`)).toBeVisible();
  await expect(page.locator('app-widget-card').filter({ hasText: 'Running mode' })).toContainText(
    'Dry runs',
  );
  await noSidewaysScroll(page);
});

test('a DCA widget on the home dashboard keeps its own plan', async ({ page }, info) => {
  const plan = `Bitcoin ${info.project.name}`;
  await page.goto('/');
  // From the defaults: an earlier run may have left its arrangement.
  const client = await api(page);
  const kept = await client.get('/api/v1/sys/dashboard_layout?filter[dashboard]=home');
  for (const layout of kept.records ?? []) {
    await client.remove(`/api/v1/sys/dashboard_layout/${layout.sys_id}`, layout._etag);
  }
  await page.goto('/');
  await page.getByRole('button', { name: 'Customize' }).click();
  await page.getByRole('button', { name: 'Add widget' }).click();
  const dialog = await opened(page);
  await dialog.getByRole('searchbox', { name: 'Search widgets or modules' }).fill('invested');
  await dialog
    .getByRole('button', { name: /^Invested\b/ })
    .first()
    .click();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Dashboard saved')).toBeVisible();

  // No choice on the home page: the card keeps its own.
  const own = page.getByRole('combobox', { name: 'DCA plan for Invested' });
  await choose(page, own, plan);
  const invested = page.locator('app-widget-card').filter({ hasText: 'Invested' }).first();
  await expect(invested.getByText(`${plan} · dry runs`)).toBeVisible();
  await page.reload();
  await expect(invested.getByText(`${plan} · dry runs`)).toBeVisible(); // kept

  await page.getByRole('button', { name: 'Customize' }).click();
  await page.getByRole('button', { name: 'Remove Invested' }).click();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(invested).toBeHidden();
});
