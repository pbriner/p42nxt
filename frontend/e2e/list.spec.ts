import { Locator, Page, TestInfo, expect, test } from '@playwright/test';

import { api, choose, cardsShown, freshList, noSidewaysScroll, opened, storage } from './helpers';

test.use({ storageState: storage('admin') });

/** A record's row: a table row, or a card on small screens. */
function row(page: Page, info: TestInfo, name: string): Locator {
  return cardsShown(info)
    ? page.getByRole('listitem', { name, exact: true })
    : page.getByRole('cell', { name, exact: true });
}

// Only the records a test looks at: other tests add people, so the first page changes.
const NARROW = '/list/sys.person?filter%5Bname%5D%5Bin%5D=System,Database,Pat%20Plain';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await freshList(page, 'sys.person');
});

test('a list sorts, and remembers the sort', async ({ page }, info) => {
  test.skip(cardsShown(info), 'cards have no headers');
  await page.goto(NARROW);
  await expect(page.getByRole('heading', { name: 'People' })).toBeVisible();
  const name = page.getByRole('columnheader', { name: 'Name' });
  await expect(page.getByRole('cell', { name: 'Pat Plain', exact: true })).toBeVisible();
  await name.getByRole('button').click();
  await expect(name).toHaveAttribute('aria-sort', 'ascending');
  await name.getByRole('button').click();
  await expect(name).toHaveAttribute('aria-sort', 'descending');
  await noSidewaysScroll(page);
  await page.goto(NARROW);
  await expect(page.getByRole('columnheader', { name: 'Name' })).toHaveAttribute(
    'aria-sort',
    'descending',
  );
});

test('filters become chips, and each chip removes its condition', async ({ page }, info) => {
  await page.goto(NARROW);
  await page.getByRole('button', { name: 'Add filter' }).click();
  const dialog = await opened(page);
  await choose(page, dialog.getByRole('combobox', { name: 'Field' }), 'Kind');
  await choose(page, dialog.getByRole('combobox', { name: 'Value' }), 'Service account');
  await page.keyboard.press('Escape');
  await dialog.getByRole('button', { name: 'Add' }).click();

  const chip = page.getByText('Kind is one of Service account');
  await expect(chip).toBeVisible();
  await expect(page).toHaveURL(/filter%5Bkind%5D%5Bin%5D=service/);
  await expect(row(page, info, 'Pat Plain')).toBeHidden();
  await expect(row(page, info, 'System')).toBeVisible();
  await page.getByRole('button', { name: 'Remove: Kind is one of Service account' }).click();
  await expect(chip).toBeHidden();
  await expect(row(page, info, 'Pat Plain')).toBeVisible();
});

test('right-click: show matching, and hide a column', async ({ page }, info) => {
  test.skip(cardsShown(info), 'the column menu belongs to the table');
  test.skip(!!info.project.use.hasTouch, 'a mouse menu');
  await page.goto(NARROW);
  await page.getByRole('cell', { name: 'Service account' }).first().click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Show matching' }).click();
  await expect(page.getByText('Kind is Service account')).toBeVisible();
  await expect(row(page, info, 'Pat Plain')).toBeHidden();

  await page.getByRole('columnheader', { name: 'Email' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Hide column' }).click();
  await expect(page.getByRole('columnheader', { name: 'Email' })).toBeHidden();
  await page.reload();
  await expect(page.getByRole('columnheader', { name: 'Email' })).toBeHidden(); // remembered

  await page.locator('.view-button').click();
  await page.getByRole('menuitem', { name: 'Reset to default' }).click();
  await expect(page.getByRole('columnheader', { name: 'Email' })).toBeVisible();
});

test('a saved view keeps the filter and can open by default', async ({ page }) => {
  await page.goto('/list/sys.person?filter%5Bkind%5D%5Bin%5D=service');
  await page.locator('.view-button').click();
  await page.getByRole('menuitem', { name: 'Save as new view…' }).click();
  const dialog = await opened(page);
  await dialog.getByLabel('Name').fill('Robots');
  await dialog.getByText('Open this list with this view by default').click();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('View “Robots” saved')).toBeVisible();

  await page.goto('/list/sys.person');
  await expect(page.locator('.view-button')).toContainText('Robots');
  await expect(page.getByText('Kind is one of Service account')).toBeVisible();

  await page.locator('.view-button').click();
  await page.getByRole('menuitem', { name: 'Delete view' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete view' }).click();
  await expect(page.locator('.view-button')).toContainText('Default view');
});

test('update selected changes each chosen record', async ({ page }, info) => {
  test.skip(cardsShown(info), 'choosing records happens in the table');
  const tag = `${info.project.name}${Date.now() % 100000}`;
  const client = await api(page);
  for (const n of [1, 2]) {
    await client.post('/api/v1/sys/person', { name: `Bulk ${tag} ${n}`, kind: 'external' });
  }
  await page.goto(`/list/sys.person?q=Bulk%20${tag}`);
  await expect(page.getByText(`Contains “Bulk ${tag}”`)).toBeVisible();
  await page.getByRole('checkbox', { name: 'Select all on this page' }).check();
  await expect(page.getByText('2 selected')).toBeVisible();
  await page.getByRole('button', { name: 'Update selected' }).click();
  const dialog = await opened(page);
  await choose(page, dialog.getByRole('combobox', { name: 'Field' }), 'Active');
  await choose(page, dialog.getByRole('combobox', { name: 'New value' }), 'No');
  await dialog.getByRole('button', { name: 'Update' }).click();
  await expect(page.getByText('2 changed')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'No', exact: true })).toHaveCount(2);
});

test('update selected never offers a read-only field', async ({ page }, info) => {
  test.skip(cardsShown(info), 'choosing records happens in the table');
  const name = `Bulk ${info.project.name}${Date.now() % 100000}`;
  const client = await api(page);
  await client.post('/api/v1/sys/connection', { type: 'dca.kraken', label: name });
  await page.goto(`/list/sys.connection?q=${encodeURIComponent(name)}`);
  await page.getByRole('checkbox', { name: 'Select all on this page' }).check();
  await page.getByRole('button', { name: 'Update selected' }).click();
  const dialog = await opened(page);
  await dialog.getByRole('combobox', { name: 'Field' }).click();
  await expect(page.getByRole('option', { name: 'Name', exact: true })).toBeVisible();
  // Whether it works comes from its check, never typed: read-only in the form, and here too.
  await expect(page.getByRole('option', { name: 'Works', exact: true })).toHaveCount(0);
});

test('deleting asks first, and can be undone', async ({ page }, info) => {
  const name = `Gone ${info.project.name} ${Date.now() % 100000}`;
  const client = await api(page);
  await client.post('/api/v1/sys/person', { name, kind: 'external' });
  await page.goto(`/list/sys.person?q=${encodeURIComponent(name)}`);
  test.skip(cardsShown(info), 'the delete button belongs to the table');
  await page.getByRole('button', { name: `Delete: ${name}` }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText(`${name} deleted`)).toBeVisible();
  await expect(page.getByRole('cell', { name, exact: true })).toBeHidden();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('cell', { name, exact: true })).toBeVisible();
});

test('small screens show cards', async ({ page }, info) => {
  test.skip(!cardsShown(info), 'cards are for small screens');
  await page.goto(NARROW);
  await expect(page.getByRole('table')).toBeHidden();
  await expect(page.getByRole('listitem', { name: 'Pat Plain' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Columns' })).toBeHidden();
  await noSidewaysScroll(page);
});

test('a list that doesn’t exist says so', async ({ page }) => {
  await page.goto('/list/nothing.here');
  await expect(page.getByRole('heading', { name: 'Not found' })).toBeVisible();
});
