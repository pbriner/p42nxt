import { expect, test } from '@playwright/test';

import { noSidewaysScroll, storage } from './helpers';

test.describe('the admin', () => {
  test.use({ storageState: storage('admin') });

  test('the bell shows unread notifications; opening one marks it read and follows it', async ({
    page,
  }, info) => {
    const title = `Failed: Nightly import (${info.project.name})`;
    await page.goto('/');
    const bell = page.getByRole('button', { name: /^Notifications, \d+ unread$/ });
    await expect(bell).toBeVisible();
    await bell.click();
    const menu = page.getByRole('menu');
    await expect(menu.getByRole('heading', { name: 'Notifications' })).toBeVisible();
    const note = menu.getByRole('menuitem', { name: new RegExp(title.replace(/[()]/g, '\\$&')) });
    await expect(note).toBeVisible();
    await expect(note).toContainText("The outside system didn't answer in time.");
    await noSidewaysScroll(page);

    await note.click();
    await expect(page).toHaveURL(/\/list\/sys\.job_run/);
    await page.getByRole('button', { name: /^Notifications/ }).click();
    await expect(page.getByRole('menu').getByText(title)).toHaveCount(0);
    await page.keyboard.press('Escape');
  });
});

test.describe('Pat', () => {
  test.use({ storageState: storage('plain') });

  test('marks all as read, then sees them all in the list', async ({ page }, info) => {
    test.skip(info.project.name !== 'desktop', 'marks them all: once is enough');
    await page.goto('/');
    await page.getByRole('button', { name: 'Notifications, 3 unread' }).click();
    const menu = page.getByRole('menu');
    await expect(menu.getByRole('menuitem', { name: /Storage is almost full/ })).toBeVisible();
    await menu.getByRole('button', { name: 'Mark all as read' }).click();
    await expect(menu.getByText('All caught up')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible();

    await menu.getByRole('menuitem', { name: 'Show all' }).click();
    await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();
    await expect(
      page.getByRole('cell', { name: 'Welcome to P42cloud', exact: true }),
    ).toBeVisible();
  });
});
