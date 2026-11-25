import { expect, test } from '@playwright/test';

import { code, noSidewaysScroll, person as personOf, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a person makes an API token, sees it once, and revokes it; sees their sessions', async ({
  page,
}, info) => {
  const name = `Script ${info.project.name} ${Date.now() % 100000}`;
  await page.goto('/profile');
  await expect(page.getByText('API tokens', { exact: true })).toBeVisible();
  await page.getByLabel('Name of the new token').fill(name);
  await page.getByRole('button', { name: 'Make a token' }).click();
  // A recent second factor first, if the last one is too long ago (a code works once: the
  // next steps' if this one was used).
  const shown = page.getByRole('status').filter({ hasText: "won't be shown again" });
  const verify = page.getByRole('dialog', { name: 'Confirm it’s you' });
  await expect(shown.or(verify)).toBeVisible();
  for (const ahead of [0, 30_000, 60_000]) {
    if (!(await verify.isVisible())) {
      break;
    }
    await verify.getByLabel('Code').fill(code(personOf('admin').key, Date.now() + ahead));
    await verify.getByRole('button', { name: 'Verify' }).click();
    await expect(shown.or(verify.getByRole('alert'))).toBeVisible();
  }
  const made = page.getByRole('status').filter({ hasText: "won't be shown again" });
  await expect(shown).toBeVisible();
  await expect(shown.locator('code')).toHaveText(/^p42_/);
  await shown.getByRole('button', { name: 'Done' }).click();
  const row = page.getByRole('listitem').filter({ hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Revoke' }).click();
  await expect(row).toHaveCount(0);

  await expect(page.getByText('Where you are signed in', { exact: true })).toBeVisible();
  await expect(page.getByText('This one')).toBeVisible();
  await noSidewaysScroll(page);
});
