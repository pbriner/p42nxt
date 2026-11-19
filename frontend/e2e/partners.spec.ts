import { expect, test } from '@playwright/test';

import { api, noSidewaysScroll, storage } from './helpers';

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

test('deleting a partner shows what goes with it, and one in use is kept', async ({
  page,
}, info) => {
  const tag = `${info.project.name} ${Date.now() % 100000}`;
  const client = await api(page);
  const kept = (await client.post('/api/v1/partners/partner', { name: `Kept ${tag}` })).sys_id;
  await client.post('/api/v1/partners/contract', {
    partner: kept,
    name: `Contract ${tag}`,
    kind: 'service',
  });
  const gone = (await client.post('/api/v1/partners/partner', { name: `Gone ${tag}` })).sys_id;
  await client.post('/api/v1/partners/contact', { partner: gone, name: `Anna ${tag}` });

  // In use by a contract: no Delete, but the way to the contract.
  await page.goto(`/form/partners.partner/${kept}`);
  await page.getByRole('button', { name: 'Delete' }).click();
  const refused = page.getByRole('dialog');
  await expect(refused.getByRole('alert')).toContainText("can't be deleted");
  await expect(refused.getByRole('button', { name: 'Delete' })).toHaveCount(0);
  await refused.getByRole('link', { name: /1 Contract/ }).click();
  await expect(page).toHaveURL(/\/list\/partners\.contract\?/);
  await expect(page.getByText(`Contract ${tag}`)).toBeVisible();

  // Its contact goes with it, after it was shown; restoring brings it back.
  await page.goto(`/form/partners.partner/${gone}`);
  await page.getByRole('button', { name: 'Delete' }).click();
  const asked = page.getByRole('dialog');
  await expect(asked.getByText('Also deleted')).toBeVisible();
  await expect(asked.getByText(`Anna ${tag}`)).toBeVisible();
  await noSidewaysScroll(page);
  await asked.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText(`Gone ${tag} deleted`)).toBeVisible();
  const contacts = await client.get(
    `/api/v1/partners/contact?filter[partner][eq]=${gone}&count=false`,
  );
  expect(contacts.records).toHaveLength(0);
});

test('contact persons are added, found, changed and removed without leaving the partner', async ({
  page,
}, info) => {
  const tag = `${info.project.name} ${Date.now() % 100000}`;
  const client = await api(page);
  const partner = (await client.post('/api/v1/partners/partner', { name: `Lines ${tag}` })).sys_id;
  await client.post('/api/v1/partners/contact', { partner, name: `Bea ${tag}` });
  await page.goto(`/form/partners.partner/${partner}`);
  await page.getByRole('tab', { name: /Contact persons/ }).click();

  // Added in a dialog: the partner is set; the form stays.
  await page.getByRole('link', { name: 'New' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(`Lines ${tag}`, { exact: true })).toBeVisible();
  await dialog.getByRole('textbox', { name: 'Name' }).fill(`Anna ${tag}`);
  await dialog.getByRole('button', { name: 'Create' }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(new RegExp(`/form/partners.partner/${partner}$`));
  const rows = page.locator('app-related-tabs tbody tr');
  await expect(rows).toHaveCount(2);

  // Sorted by a column, and searched.
  await page.getByRole('button', { name: 'Name', exact: true }).click();
  await expect(rows.first()).toContainText(`Anna ${tag}`);
  await page.getByRole('searchbox', { name: /Search/ }).fill('Bea');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText(`Bea ${tag}`);
  await noSidewaysScroll(page);

  // Changed in a dialog.
  await rows.first().click();
  await dialog.getByRole('textbox', { name: 'Role' }).fill('Accounts');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();

  // Removed, and back with Undo.
  await page.getByRole('button', { name: `Delete Bea ${tag}` }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
  await expect(rows).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(rows).toHaveCount(1);
  const back = await client.get(`/api/v1/partners/contact?filter[partner][eq]=${partner}`);
  expect(back.records.map((c: { role: string | null }) => c.role)).toContain('Accounts');
});
