import { expect, test } from '@playwright/test';

import { choose, compact, noSidewaysScroll, opened, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a widget is made from a list, its fields picked from the list, with a preview', async ({
  page,
}, info) => {
  test.skip(
    compact(info),
    'the view menu and the form are the same; once on a wide screen is enough',
  );
  const title = `Kinds of persons ${info.project.name} ${Date.now() % 100000}`;
  await page.goto('/list/sys.person');
  await page
    .getByRole('button', { name: /All|Default/ })
    .first()
    .click();
  await page.getByRole('menuitem', { name: 'Create widget from this list' }).click();
  const dialog = await opened(page);
  await dialog.getByLabel('Title').fill(title);
  await dialog.getByRole('radio', { name: /A chart/ }).click();
  await choose(page, dialog.getByRole('combobox', { name: 'Grouped by' }), 'Kind');
  await dialog.getByRole('button', { name: 'Create' }).click();

  await expect(page).toHaveURL(/\/form\/sys\.widget\//);
  const preview = page.locator('app-widget-preview');
  await expect(preview.getByText(title)).toBeVisible();
  await expect(preview.locator('app-chart')).toBeVisible();
  // The measured field is picked from the list's number fields, its group from the others.
  await expect(page.getByRole('combobox', { name: 'Grouped by' })).toContainText('Kind');
  await noSidewaysScroll(page);
});
