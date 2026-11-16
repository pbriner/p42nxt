import { expect, test } from '@playwright/test';

import { api, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

const PDF = Buffer.from('%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n');

test('files are attached to a record, opened and removed', async ({ page }, info) => {
  await page.goto('/');
  const made = await (
    await api(page)
  ).post('/api/v1/hello/greeting', {
    text: `With a receipt ${info.project.name} ${Date.now() % 100000}`,
  });
  await page.goto(`/form/hello.greeting/${made.sys_id}`);
  const files = page.locator('app-record-files');
  await expect(files.getByText('No files attached.')).toBeVisible();

  await files.locator('#record-files-picker').setInputFiles({
    name: 'Heating oil.pdf',
    mimeType: 'application/pdf',
    buffer: PDF,
  });
  const link = files.getByRole('link', { name: 'Heating oil.pdf', exact: true });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', /\/api\/v1\/files\/.+\?inline=true$/);
  const got = await page.request.get((await link.getAttribute('href')) ?? '');
  expect(got.status()).toBe(200);
  expect(await got.body()).toEqual(PDF);
  await noSidewaysScroll(page);

  // Not every kind of file: a web page is refused and says why.
  await files.locator('#record-files-picker').setInputFiles({
    name: 'page.html',
    mimeType: 'text/html',
    buffer: Buffer.from('<script>alert(1)</script>'),
  });
  await expect(files.getByRole('alert')).toContainText("isn't accepted");

  await files.getByRole('button', { name: 'Remove Heating oil.pdf' }).click();
  await expect(files.getByText('No files attached.')).toBeVisible();
});
