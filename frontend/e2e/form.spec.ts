import { expect, test } from '@playwright/test';

import { api, code, noSidewaysScroll, opened, person as personOf, storage } from './helpers';

test.use({ storageState: storage('admin') });

async function person(page: import('@playwright/test').Page, name: string): Promise<string> {
  const client = await api(page);
  const made = await client.post('/api/v1/sys/person', { name, kind: 'external' });
  return made.sys_id as string;
}

test('a record opens from its list, and changes are saved', async ({ page }, info) => {
  await page.goto('/');
  const name = `Form ${info.project.name} ${Date.now() % 100000}`;
  const id = await person(page, name);
  await page.goto(`/form/sys.person/${id}`);
  await expect(page.getByRole('heading', { name: `Person ${name}` })).toBeVisible();
  await noSidewaysScroll(page);
  const save = page.getByRole('button', { name: 'Save' });
  await expect(save).toBeDisabled();
  await page.getByLabel('Name').fill(`${name} changed`);
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: `Person ${name} changed` })).toBeVisible();
  await expect(page.getByText(/Created .* by /)).toBeVisible();
});

test('a new record is created and opens', async ({ page }, info) => {
  await page.goto('/list/sys.group');
  await page.getByRole('button', { name: 'New' }).click();
  await expect(page.getByRole('heading', { name: 'New Group' })).toBeVisible();
  const create = page.getByRole('button', { name: 'Create' });
  await page
    .getByLabel('Name', { exact: true })
    .fill(`Team ${info.project.name} ${Date.now() % 100000}`);
  await create.click();
  await expect(page.getByText('Group created')).toBeVisible();
  await expect(page).toHaveURL(/\/form\/sys\.group\/[0-9a-f-]{36}$/);
  await expect(page.getByRole('heading', { name: 'Related' })).toBeVisible();
});

test('related records show in tabs with their count', async ({ page }, info) => {
  await page.goto('/');
  const client = await api(page);
  const group = await client.post('/api/v1/sys/group', {
    name: `Crew ${info.project.name} ${Date.now() % 100000}`,
  });
  const member = await person(page, `Member ${info.project.name}`);
  await client.post('/api/v1/sys/membership', { group: group.sys_id, person: member });
  await page.goto(`/form/sys.group/${group.sys_id}`);
  const tab = page.getByRole('tab', { name: /Memberships/ });
  await expect(tab).toContainText('1');
  await expect(page.getByRole('cell', { name: `Member ${info.project.name}` })).toBeVisible();
});

test('when someone else saved in between, the person chooses per field', async ({ page }, info) => {
  await page.goto('/');
  const name = `Clash ${info.project.name} ${Date.now() % 100000}`;
  const id = await person(page, name);
  await page.goto(`/form/sys.person/${id}`);
  await page.getByLabel('Name').fill(`${name} mine`);
  // Someone else changes the same field meanwhile.
  const fresh = await page.request.get(`/api/v1/sys/person/${id}`);
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === 'p42_csrf')!.value;
  await page.request.patch(`/api/v1/sys/person/${id}`, {
    data: { name: `${name} theirs` },
    headers: { 'If-Match': fresh.headers()['etag']!, 'X-CSRF-Token': csrf },
  });
  await page.getByRole('button', { name: 'Save' }).click();
  const conflict = page.getByRole('alert').filter({ hasText: 'Someone else changed this record' });
  await expect(conflict).toBeVisible();
  await expect(conflict.getByText(`${name} theirs`)).toBeVisible();
  await conflict.getByRole('button', { name: 'Save with these values' }).click();
  await expect(page.getByRole('heading', { name: `Person ${name} mine` })).toBeVisible();
});

test('unsaved changes are offered back after leaving by accident', async ({ page }, info) => {
  await page.goto('/');
  const name = `Draft ${info.project.name} ${Date.now() % 100000}`;
  const id = await person(page, name);
  await page.goto(`/form/sys.person/${id}`);
  await page.getByLabel('Name').fill(`${name} unsaved`);
  page.on('dialog', (dialog) => void dialog.accept());
  await page.goto('/');
  await page.goto(`/form/sys.person/${id}`);
  await expect(page.getByText('You have unsaved changes from before.')).toBeVisible();
  await page.getByRole('button', { name: 'Restore them' }).click();
  await expect(page.getByLabel('Name')).toHaveValue(`${name} unsaved`);
  await expect(page.getByRole('button', { name: 'Save' })).toBeEnabled();
});

test('deleting from the form asks first, and can be undone', async ({ page }, info) => {
  await page.goto('/');
  const name = `Bye ${info.project.name} ${Date.now() % 100000}`;
  const id = await person(page, name);
  await page.goto(`/list/sys.person?q=${encodeURIComponent(name)}`);
  await page.goto(`/form/sys.person/${id}`);
  await page.getByRole('button', { name: 'Delete' }).click();
  const dialog = await opened(page);
  await dialog.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText(`${name} deleted`)).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('heading', { name: `Person ${name}` })).toBeVisible();
});

test.describe('without the right to change', () => {
  test.use({ storageState: storage('plain') });
  test('a record one may only read says so and shows its values', async ({ page }) => {
    await page.goto('/list/sys.person?q=Ada');
    await page.goto('/');
    const found = await (await api(page)).get('/api/v1/sys/person?filter[name]=Ada%20Admin');
    await page.goto(`/form/sys.person/${found.records[0].sys_id}`);
    await expect(page.getByText('You can view this person, but not change it.')).toBeVisible();
    await expect(page.getByText('Ada Admin').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save' })).toBeHidden();
  });
});

test('a change that needs the code again asks for it, then goes through', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'desktop', 'one code per step: once is enough');
  await page.goto('/');
  const name = `Verify ${Date.now() % 100000}`;
  const id = await person(page, name);
  // The server answers the first save as for a second factor older than its limit.
  let asked = false;
  await page.route(`**/api/v1/sys/person/${id}`, async (route) => {
    if (route.request().method() === 'PATCH' && !asked) {
      asked = true;
      await route.fulfill({
        status: 403,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          code: 'verify_again',
          title: 'Enter a code from your authenticator app again to change access.',
        }),
      });
      return;
    }
    await route.fallback();
  });
  await page.goto(`/form/sys.person/${id}`);
  await page.getByLabel('Name').fill(`${name} changed`);
  await page.getByRole('button', { name: 'Save' }).click();
  // The save waits for the code meanwhile (its spinner turns), so not `opened()`.
  const dialog = page.getByRole('dialog', { name: 'Confirm it’s you' });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Code').fill('000000');
  await dialog.getByRole('button', { name: 'Verify' }).click();
  await expect(dialog.getByRole('alert')).toBeVisible(); // a wrong code: says so, stays open
  // The next step's code: the sign-in used this one, and a code works once.
  await dialog.getByLabel('Code').fill(code(personOf('admin').key, Date.now() + 30_000));
  await dialog.getByRole('button', { name: 'Verify' }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: `Person ${name} changed` })).toBeVisible();
});

test('a record shows who changed what, when', async ({ page }, info) => {
  await page.goto('/');
  const name = `History ${info.project.name} ${Date.now() % 100000}`;
  const id = await person(page, name);
  await page.goto(`/form/sys.person/${id}`);
  await page.getByLabel('Name').fill(`${name} changed`);
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Show the history' }).click();
  const history = page.locator('app-record-history');
  await expect(history.getByText('Changed', { exact: true })).toBeVisible();
  await expect(history.getByText(`${name} changed`)).toBeVisible();
  await expect(history.getByText('Created', { exact: true })).toBeVisible();
});
