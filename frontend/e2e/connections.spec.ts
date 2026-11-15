import { expect, test } from '@playwright/test';

import { storage } from './helpers';

test.use({ storageState: storage('plain') });

test('a connection: its type picked by name, then its keys asked for', async ({ page }, info) => {
  const name = `Kraken ${info.project.name} ${Date.now() % 100000}`;
  await page.goto('/form/sys.connection/new');
  await expect(page.getByRole('heading', { name: 'New Connection' })).toBeVisible();
  await expect(page.getByLabel('Type name')).toHaveCount(0); // the link stands for it
  await expect(page.getByText('Works', { exact: true })).toHaveCount(0); // the check's, later
  const type = page.getByRole('combobox', { name: 'Type' });
  await type.fill('Kraken');
  await page.getByRole('option', { name: 'Kraken' }).first().click();
  await page.getByRole('textbox', { name: 'Name' }).fill(name);
  await page.getByRole('button', { name: 'Create' }).click();

  await expect(page.getByRole('heading', { name: `Connection ${name}` })).toBeVisible();
  const key = page.getByLabel('API key', { exact: true });
  await expect(key).toBeVisible();
  await expect(page.getByLabel('API secret', { exact: true })).toBeVisible();
  await key.fill('not-a-real-key');
  const keys = page.locator('app-connection-secrets');
  await keys.getByRole('button', { name: 'Save' }).first().click();
  await expect(page.getByText('API key saved, encrypted.')).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Works' })).toHaveCount(0); // shown, not edited
});
