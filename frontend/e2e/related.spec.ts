import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { api, opened, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a person chooses and orders a form’s related lists, and they stay', async ({
  page,
}, info) => {
  await page.goto('/');
  const client = await api(page);
  const group = await client.post('/api/v1/sys/group', {
    name: `Lists ${info.project.name} ${Date.now() % 100000}`,
  });
  const person = await client.post('/api/v1/sys/person', { name: `Member ${info.project.name}` });
  await client.post('/api/v1/sys/membership', { group: group.sys_id, person: person.sys_id });
  // Start from the default for this form.
  const kept = await client.get('/api/v1/sys/related_layout?filter[form]=sys.group');
  for (const layout of kept.records ?? []) {
    await client.remove(`/api/v1/sys/related_layout/${layout.sys_id}`, layout._etag);
  }

  await page.goto(`/form/sys.group/${group.sys_id}`);
  await expect(page.getByRole('tab', { name: /Memberships/ })).toBeVisible(); // it has records
  await expect(page.getByRole('tab', { name: /Roles of groups/ })).toBeHidden(); // none yet

  await page.getByRole('button', { name: 'Related lists…' }).click();
  let dialog = await opened(page);
  // Checked once the fade-in is over: mid-fade every text has too little contrast.
  await expect.poll(() => page.evaluate(() => document.getAnimations().length)).toBe(0);
  const axe = await new AxeBuilder({ page })
    .include('mat-dialog-container')
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
    .analyze();
  expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)).toEqual(
    [],
  );
  await dialog.getByRole('option', { name: /Roles of groups/ }).click();
  await dialog.getByRole('button', { name: 'Add the chosen' }).click();
  await dialog.getByRole('option', { name: /Roles of groups/ }).click();
  await dialog.getByRole('button', { name: 'Move up' }).click();
  await dialog.getByRole('button', { name: 'Apply' }).click();
  const tabs = page.getByRole('tab');
  await expect(tabs.first()).toContainText('Roles of groups'); // first, as ordered

  await page.reload();
  await expect(page.getByRole('tab').first()).toContainText('Roles of groups'); // kept for them

  await page.getByRole('button', { name: 'Related lists…' }).click();
  dialog = await opened(page);
  await dialog.getByRole('option', { name: /Memberships/ }).click();
  await dialog.getByRole('button', { name: 'Remove the chosen' }).click();
  await dialog.getByRole('button', { name: 'Apply' }).click();
  await expect(page.getByRole('tab', { name: /Memberships/ })).toBeHidden();

  await page.getByRole('button', { name: 'Related lists…' }).click();
  dialog = await opened(page);
  await dialog.getByRole('button', { name: 'Reset to default' }).click();
  await expect(page.getByRole('tab', { name: /Memberships/ })).toBeVisible();
  await expect(page.getByRole('tab', { name: /Roles of groups/ })).toBeHidden();
});

test('records in a related list link to their own forms', async ({ page }, info) => {
  await page.goto('/');
  const client = await api(page);
  const name = `Linked ${info.project.name} ${Date.now() % 100000}`;
  const group = await client.post('/api/v1/sys/group', { name: `Team of ${name}` });
  const person = await client.post('/api/v1/sys/person', { name });
  await client.post('/api/v1/sys/membership', { group: group.sys_id, person: person.sys_id });

  await page.goto(`/form/sys.group/${group.sys_id}`);
  await page.getByRole('tab', { name: /Memberships/ }).click();
  await page.getByRole('link', { name }).click(); // the person, from the group
  await expect(page).toHaveURL(new RegExp(`/form/sys.person/${person.sys_id}$`));
  await page.getByRole('tab', { name: /Memberships/ }).click();
  await page.getByRole('link', { name: `Team of ${name}` }).click(); // and back to the group
  await expect(page).toHaveURL(new RegExp(`/form/sys.group/${group.sys_id}$`));
});

test('a new record from a related list shows the record it belongs to by name', async ({
  page,
}, info) => {
  await page.goto('/');
  const client = await api(page);
  const name = `Named ${info.project.name} ${Date.now() % 100000}`;
  const group = await client.post('/api/v1/sys/group', { name });
  const person = await client.post('/api/v1/sys/person', { name: `Member of ${name}` });
  await client.post('/api/v1/sys/membership', { group: group.sys_id, person: person.sys_id });

  await page.goto(`/form/sys.group/${group.sys_id}`);
  await page.getByRole('tab', { name: /Memberships/ }).click();
  await page.getByRole('link', { name: 'New' }).click();
  await expect(page.getByRole('heading', { name: 'New Membership' })).toBeVisible();
  const field = page.getByRole('combobox', { name: 'Group' });
  await expect(field).toHaveValue(name); // its name, not its ID
});

test('a long related list turns its pages', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'paging works the same at every size');
  await page.goto('/');
  const client = await api(page);
  const name = `Paged ${Date.now() % 100000}`;
  const group = await client.post('/api/v1/sys/group', { name });
  for (let n = 0; n < 25; n++) {
    const person = await client.post('/api/v1/sys/person', { name: `${name} member ${n}` });
    await client.post('/api/v1/sys/membership', { group: group.sys_id, person: person.sys_id });
  }
  await page.goto(`/form/sys.group/${group.sys_id}`);
  await page.getByRole('tab', { name: /Memberships/ }).click();
  await expect(page.getByText('1–20 of 25')).toBeVisible();
  await page.getByRole('button', { name: 'Next page' }).click();
  await expect(page.getByText('21–25 of 25')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled();
  await page.getByRole('button', { name: 'Previous page' }).click();
  await expect(page.getByText('1–20 of 25')).toBeVisible();
});
