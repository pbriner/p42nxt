import { expect, test } from '@playwright/test';

import { api, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a cost center under a house shows the house as its profit center', async ({ page }, info) => {
  const tag = `${info.project.name}-${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  await client.post('/api/v1/costing/center', {
    code: `H-${tag}`,
    name: `House ${tag}`,
    kind: 'profit',
  });

  await page.goto('/form/costing.center/new');
  await page.getByRole('textbox', { name: 'Code' }).fill(`HEAT-${tag}`);
  await page.getByRole('textbox', { name: 'Name' }).fill(`Heating ${tag}`);
  const parent = page.getByRole('combobox', { name: 'Part of' });
  await parent.fill(`House ${tag}`);
  await page.getByRole('option', { name: `House ${tag}` }).click();
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByText('Center created')).toBeVisible();
  // Worked out from where it stands, not typed in.
  await expect(page.getByText(`House ${tag}`).first()).toBeVisible();
  await noSidewaysScroll(page);

  await page.goto(`/list/costing.center?q=${encodeURIComponent(`Heating ${tag}`)}`);
  const entry = page
    .getByRole('row', { name: new RegExp(`Heating ${tag}`) })
    .or(page.getByRole('listitem', { name: new RegExp(`Heating ${tag}`) }));
  // A record of a tree is named by its path.
  await expect(entry).toContainText(`House ${tag} › Heating ${tag}`);
});

test('an order moves along its flow by its buttons', async ({ page }, info) => {
  const tag = `${info.project.name}-${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  const house = await client.post('/api/v1/costing/center', {
    code: `HO-${tag}`,
    name: `House ${tag}`,
    kind: 'profit',
  });
  const order = await client.post('/api/v1/costing/order', {
    code: `O-${tag}`,
    name: `Paint ${tag}`,
    center: house.sys_id,
  });
  await page.goto(`/form/costing.order/${order.sys_id}`);
  await expect(page.getByRole('button', { name: 'Reopen' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Mark done' }).click();
  await expect(page.getByRole('button', { name: 'Reopen' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Mark done' })).toHaveCount(0);
  await noSidewaysScroll(page);
});

test('an order has a conversation, kept as plain text', async ({ page }, info) => {
  const tag = `${info.project.name}-${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  const house = await client.post('/api/v1/costing/center', {
    code: `HC-${tag}`,
    name: `House ${tag}`,
    kind: 'profit',
  });
  const order = await client.post('/api/v1/costing/order', {
    code: `C-${tag}`,
    name: `Roof ${tag}`,
    center: house.sys_id,
  });
  await page.goto(`/form/costing.order/${order.sys_id}`);
  const journal = page.locator('app-record-journal');
  // The journal starts with the order being created; there's nothing said yet.
  await expect(journal.getByText('Created', { exact: true })).toBeVisible();
  await journal.getByLabel('Write a comment').fill('<b>Offer</b> from the roofer is in.');
  await journal.getByRole('button', { name: 'Comment', exact: true }).click();
  const said = journal.getByText('<b>Offer</b> from the roofer is in.');
  await expect(said).toBeVisible();
  // Only what was said, then only the changes.
  await journal.getByRole('radio', { name: 'Notes and comments' }).click();
  await expect(journal.getByText('Created', { exact: true })).toHaveCount(0);
  await journal.getByRole('radio', { name: 'Changes' }).click();
  await expect(said).toHaveCount(0);
  await journal.getByRole('radio', { name: 'All' }).click();
  await journal.getByRole('button', { name: 'Remove my comment' }).click();
  await expect(said).toHaveCount(0);
  await noSidewaysScroll(page);
});
