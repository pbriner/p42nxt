import { expect, test } from '@playwright/test';

import { compact, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('home greets the person and the navigator shows what they may open', async ({
  page,
}, info) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Hello, Ada Admin' })).toBeVisible();
  await noSidewaysScroll(page);
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  if (compact(info)) {
    await expect(nav).toBeHidden();
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  }
  await expect(nav).toBeVisible();
  await expect(nav.getByText('Platform', { exact: true })).toBeVisible();
  await nav.getByRole('button', { name: 'Hello' }).click();
  await expect(nav.getByRole('link', { name: 'Greetings' })).toBeVisible();

  await nav.getByPlaceholder('Filter navigator').fill('themes');
  await expect(nav.getByRole('link', { name: 'Themes' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Greetings' })).toBeHidden();
  await nav.getByPlaceholder('Filter navigator').fill('zzzz');
  await expect(nav.getByText('Nothing matches “zzzz”.')).toBeVisible();
  await nav.getByRole('button', { name: 'Clear the filter' }).click();

  await nav.getByRole('link', { name: 'Greetings' }).click();
  await expect(page).toHaveURL(/\/list\/hello\.greeting/);
  await expect(page.getByRole('heading', { name: 'Greetings' })).toBeVisible();
  if (compact(info)) {
    await expect(nav).toBeHidden(); // the drawer closes after choosing
  } else {
    await expect(nav.getByRole('link', { name: 'Greetings' })).toHaveClass(/active/);
  }
});

test('favorites: star an item, find it under Favorites, remove it', async ({ page }, info) => {
  await page.goto('/');
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  if (compact(info)) {
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  }
  await nav.getByRole('button', { name: 'Core' }).click();
  const star = nav.getByRole('button', { name: 'Add to favorites: People' });
  await star.click();
  await expect(nav.getByRole('button', { name: 'Remove from favorites: People' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await nav.getByRole('radio', { name: 'Favorites' }).click();
  await expect(nav.getByRole('link', { name: /People/ })).toBeVisible();
  await page.reload();
  if (compact(info)) {
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  }
  await nav.getByRole('radio', { name: 'Favorites' }).click();
  await nav.getByRole('button', { name: 'Remove from favorites: People' }).click();
  await expect(nav.getByText('No favorites yet.', { exact: false })).toBeVisible();
});

test('signing out ends the session', async ({ page, context }, info) => {
  test.skip(info.project.name !== 'desktop', 'once is enough; it ends the shared session');
  await context.clearCookies();
  await page.goto('/');
  await expect(page).toHaveURL(/\/sign-in/);
});
