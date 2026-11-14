import { Page, expect, test } from '@playwright/test';

import { api, opened, storage } from './helpers';

test.use({ storageState: storage('plain') });

/** Back to the defaults: the person's arrangement goes. */
async function freshDashboard(page: Page, name: string): Promise<void> {
  await page.goto('/');
  const client = await api(page);
  const found = await client.get(`/api/v1/sys/dashboard_layout?filter[dashboard]=${name}`);
  for (const layout of found.records ?? []) {
    await client.remove(`/api/v1/sys/dashboard_layout/${layout.sys_id}`, layout._etag);
  }
}

async function greetings(page: Page): Promise<void> {
  const client = await api(page);
  for (const [text, language] of [
    ['Hallo', 'de'],
    ['Salut', 'fr'],
    ['Hi', 'en'],
  ]) {
    await client.post('/api/v1/hello/greeting', { text, language, pinned: true });
  }
}

test.beforeEach(async ({ page }) => {
  await freshDashboard(page, 'home');
  await freshDashboard(page, 'hello');
});

test('home shows the widgets offered by default', async ({ page }) => {
  await greetings(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Hello, Pat Plain' })).toBeVisible();
  const card = page.locator('app-widget-card').filter({ hasText: 'Greetings' });
  await expect(card.getByRole('link', { name: /^\d+$/ })).toBeVisible();
  await card.getByRole('link', { name: 'Show all' }).click();
  await expect(page).toHaveURL(/\/list\/hello\.greeting/);
});

test('a person adds, keeps and removes widgets', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Customize' }).click();
  await page.getByRole('button', { name: 'Add widget' }).click();
  const dialog = await opened(page);
  await dialog.getByRole('searchbox', { name: 'Search widgets or modules' }).fill('people');
  await dialog.getByRole('button', { name: /People/ }).click();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Dashboard saved')).toBeVisible();
  await page.reload();
  const people = page.locator('app-widget-card').filter({ hasText: 'People' });
  await expect(people).toBeVisible();

  await page.getByRole('button', { name: 'Customize' }).click();
  await page.getByRole('button', { name: 'Remove People' }).click();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(people).toBeHidden();
});

test('a module’s dashboard: lists, charts that become tables, keyboard order', async ({ page }) => {
  await greetings(page);
  await page.goto('/dashboard/hello');
  await expect(page.getByRole('heading', { name: 'Hello', exact: true })).toBeVisible();
  const donut = page.locator('app-widget-card').filter({ hasText: 'By language' });
  await expect(donut.getByText('total')).toBeVisible();
  await donut.getByRole('button', { name: 'Show as a table' }).click();
  await expect(donut.getByRole('rowheader', { name: 'German' })).toBeVisible();
  const pinned = page.locator('app-widget-card').filter({ hasText: 'Pinned greetings' });
  await expect(pinned.getByRole('link', { name: /Hallo/ })).toBeVisible();

  await page.getByRole('button', { name: 'Customize' }).click();
  const titles = page.locator('app-widget-card .title');
  const before = await titles.allTextContents();
  await page.getByRole('button', { name: `Move ${before[0]} later` }).click();
  await expect(titles.nth(1)).toHaveText(before[0]!);
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(titles.first()).toHaveText(before[0]!);
});
