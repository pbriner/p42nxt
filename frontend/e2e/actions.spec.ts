import { Page, TestInfo, expect, test } from '@playwright/test';

import { api, choose, cardsShown, opened, storage } from './helpers';

test.use({ storageState: storage('admin') });

async function greeting(page: Page, info: TestInfo, words: string): Promise<string> {
  await page.goto('/');
  const made = await (
    await api(page)
  ).post('/api/v1/hello/greeting', {
    text: `${words} ${info.project.name} ${Date.now() % 100000}`,
  });
  return made.sys_id as string;
}

test('a button does what the record offers, and the offer follows the record', async ({
  page,
}, info) => {
  const id = await greeting(page, info, 'Pin me');
  await page.goto(`/form/hello.greeting/${id}`);
  await page.getByRole('button', { name: 'Pin' }).click();
  await expect(page.getByText('Pinned.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Pin', exact: true })).toBeHidden();
  await page.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Unpin' }).click();
  await expect(page.getByText('Unpinned.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Pin', exact: true })).toBeVisible();
});

test('an action asks for what it needs, and runs the module’s code', async ({ page }, info) => {
  const id = await greeting(page, info, 'Sign me');
  await page.goto(`/form/hello.greeting/${id}`);
  await page.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Sign' }).click();
  const dialog = await opened(page);
  const go = dialog.getByRole('button', { name: 'Sign' });
  await expect(go).toBeDisabled();
  await dialog.getByLabel('Signature').fill('Ada');
  await go.click();
  await expect(page.getByText('Signed by Ada.')).toBeVisible();
  await expect(page.getByRole('heading', { name: /— Ada$/ })).toBeVisible();
});

test('form rules show and require fields as the record changes', async ({ page }) => {
  await page.goto('/form/hello.greeting/new');
  await expect(page.getByRole('heading', { name: 'New Greeting' })).toBeVisible();
  await expect(page.getByLabel('Team')).toBeHidden();
  await page.getByLabel('Text').fill('For my team');
  await choose(page, page.getByRole('combobox', { name: 'For' }), 'One team');
  await expect(page.getByLabel('Team')).toBeVisible();
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByText('Team is required')).toBeVisible();
  await choose(page, page.getByRole('combobox', { name: 'For' }), 'Everyone');
  await expect(page.getByLabel('Team')).toBeHidden();
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByText('Greeting created')).toBeVisible();
});

test('an action on several records at once', async ({ page }, info) => {
  test.skip(cardsShown(info), 'choosing records happens in the table');
  const tag = `Bulk pin ${info.project.name} ${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  for (const n of [1, 2]) {
    await client.post('/api/v1/hello/greeting', { text: `${tag} ${n}` });
  }
  await page.goto(`/list/hello.greeting?q=${encodeURIComponent(tag)}`);
  await page.getByRole('checkbox', { name: 'Select all on this page' }).check();
  await page
    .getByRole('region', { name: '2 selected' })
    .getByRole('button', { name: 'Pin' })
    .click();
  await expect(page.getByText('Pin: done')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Yes', exact: true })).toHaveCount(2);
});
