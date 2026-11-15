import { expect, test } from '@playwright/test';

import { api, noSidewaysScroll, storage } from './helpers';

test.describe('the admin', () => {
  test.use({ storageState: storage('admin') });

  test('a message shows in the band under the top bar and can be closed', async ({
    page,
  }, info) => {
    const subject = `Planned maintenance (${info.project.name})`;
    await page.goto('/');
    const client = await api(page);
    const made = await client.post('/api/v1/sys/announcement', {
      kind: 'information',
      subject,
      text: 'The app is unavailable on Sunday morning.',
      valid_until: new Date(Date.now() + 5 * 60_000).toISOString(),
    });
    try {
      await page.reload();
      const band = page.getByRole('status').filter({ hasText: subject });
      await expect(band).toBeVisible();
      await expect(band).toContainText('The app is unavailable on Sunday morning.');
      await noSidewaysScroll(page);
      await band.getByRole('button', { name: `Close the message ${subject}` }).click();
      await expect(band).toHaveCount(0);
      await page.reload();
      await expect(page.getByRole('status').filter({ hasText: subject })).toHaveCount(0);
    } finally {
      await client.remove(`/api/v1/sys/announcement/${made.sys_id}`, made._etag);
    }
  });
});

test.describe('Pat', () => {
  test.use({ storageState: storage('plain') });

  test('sees messages for everyone, not those for a group they are not in', async ({
    page,
    browser,
  }, info) => {
    test.skip(info.project.name !== 'desktop', 'who sees what: once is enough');
    const admin = await browser.newContext({ storageState: storage('admin') });
    const adminPage = await admin.newPage();
    await adminPage.goto('/');
    const client = await api(adminPage);
    const until = new Date(Date.now() + 5 * 60_000).toISOString();
    const group = await client.post('/api/v1/sys/group', { name: `Not Pat ${Date.now()}` });
    const forAll = await client.post('/api/v1/sys/announcement', {
      kind: 'warning',
      subject: 'For everyone',
      valid_until: until,
    });
    const forGroup = await client.post('/api/v1/sys/announcement', {
      kind: 'warning',
      subject: 'Only for the group',
      audience: group.sys_id,
      valid_until: until,
    });
    try {
      await page.goto('/');
      await expect(page.getByRole('alert').filter({ hasText: 'For everyone' })).toBeVisible();
      await expect(page.getByRole('alert').filter({ hasText: 'Only for the group' })).toHaveCount(
        0,
      );
    } finally {
      await client.remove(`/api/v1/sys/announcement/${forAll.sys_id}`, forAll._etag);
      await client.remove(`/api/v1/sys/announcement/${forGroup.sys_id}`, forGroup._etag);
      await client.remove(`/api/v1/sys/group/${group.sys_id}`, group._etag);
      await admin.close();
    }
  });
});
