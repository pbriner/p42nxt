import { expect, test } from '@playwright/test';

import { storage } from './helpers';

test.use({ storageState: storage('admin') });

test('an administrator runs a schedule now, sees the run, and pauses it', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'desktop', 'one worker for all sizes: once is enough');
  await page.goto('/list/sys.schedule');
  await expect(page.getByRole('heading', { name: 'Schedules' })).toBeVisible();
  await page.getByRole('cell', { name: 'Dynamic groups, nightly', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: /Schedule Dynamic groups, nightly/ }),
  ).toBeVisible();

  await page.getByRole('button', { name: 'Run now' }).click();
  await expect(page.getByText('Queued: it starts as soon as a worker is free.')).toBeVisible();

  // The worker picks it up within seconds.
  await expect(async () => {
    await page.goto(
      '/list/sys.job_run?filter%5Bjob%5D=sys.repair_groups&filter%5Btrigger%5D=manual',
    );
    await expect(page.getByRole('cell', { name: 'Succeeded', exact: true }).first()).toBeVisible({
      timeout: 1000,
    });
  }).toPass({ timeout: 20_000 });
  await expect(page.getByRole('cell', { name: /groups checked\./ }).first()).toBeVisible();

  await page.goto('/list/sys.schedule');
  await page.getByRole('cell', { name: 'Dynamic groups, nightly', exact: true }).click();
  await page.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Pause' }).click();
  await expect(page.getByText('Paused.')).toBeVisible();
  await page.getByRole('button', { name: 'Resume' }).click();
  await expect(page.getByText('Resumed.')).toBeVisible();
});
