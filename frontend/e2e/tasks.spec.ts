import { expect, test } from '@playwright/test';

import { api, compact, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a group member takes a task, keeps work notes apart from comments, and marks it done', async ({
  page,
}, info) => {
  const tag = `${info.project.name}-${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  const desk = (await client.get('/api/v1/sys/group?filter[name]=Help desk')).records[0].sys_id;
  const task = await client.post('/api/v1/tasks/task', {
    title: `Printer jammed ${tag}`,
    group: desk,
    impact: 'high',
    urgency: 'high',
  });
  await page.goto(`/form/tasks.task/${task.sys_id}`);
  await expect(page.getByText('Critical').first()).toBeVisible();

  await page.getByRole('button', { name: 'Take' }).click();
  await expect(page.getByRole('button', { name: 'Give back' })).toBeVisible();

  const journal = page.locator('app-record-journal');
  // A work note is what's offered first: it stays with the group.
  await expect(journal.getByRole('radio', { name: 'Work note', exact: true })).toBeChecked();
  await journal.getByLabel('Write a work note').fill('Spare drum is in the cupboard.');
  await journal.getByRole('button', { name: 'Add note' }).click();
  await expect(journal.getByText('Spare drum is in the cupboard.')).toBeVisible();
  await journal.getByRole('radio', { name: 'Comment', exact: true }).click();
  await journal.getByLabel('Write a comment').fill('We are on it.');
  await journal.getByRole('button', { name: 'Comment', exact: true }).click();
  await expect(journal.getByText('We are on it.')).toBeVisible();
  await expect(journal.locator('.note')).toHaveCount(1);

  await page.getByRole('button', { name: 'Mark done' }).click();
  await expect(page.getByRole('button', { name: 'Take' })).toHaveCount(0);
  await expect(journal.getByText('Done').first()).toBeVisible();
  await noSidewaysScroll(page);
});

test('of the items leading to the task list, only the one opened is marked', async ({
  page,
}, info) => {
  test.skip(compact(info), 'the navigator closes after choosing on small screens');
  await page.goto('/');
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  const tasks = nav.getByRole('button', { name: 'Tasks', exact: true });
  if ((await tasks.getAttribute('aria-expanded')) !== 'true') {
    await tasks.click();
  }
  const items = ['My work', "My groups' work", 'All tasks'];
  for (const chosen of items) {
    await nav.getByRole('link', { name: chosen, exact: true }).click();
    for (const item of items) {
      const link = nav.getByRole('link', { name: item, exact: true });
      if (item === chosen) {
        await expect(link).toHaveClass(/active/);
      } else {
        await expect(link).not.toHaveClass(/active/);
      }
    }
  }
});
