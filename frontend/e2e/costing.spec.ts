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
