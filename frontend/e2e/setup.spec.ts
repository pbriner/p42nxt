import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { code, noSidewaysScroll } from './helpers';

const PORT = Number(process.env['P42_E2E_PORT'] ?? 8898) - 1;
const FRESH = `http://localhost:${PORT}`;

// The fresh installation can be set up once: one size does it.
test.use({ baseURL: FRESH, extraHTTPHeaders: { Origin: FRESH } });

test('a new installation is set up in the browser, then its admin sets up a second factor', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'desktop', 'an installation is set up once');
  // The open step's: earlier ones may still be closing.
  const next = () =>
    page.getByRole('button', { name: 'Next' }).filter({ visible: true }).last().click();
  await page.goto('/sign-in');
  await expect(page).toHaveURL(/\/setup$/); // not set up yet: to the installer
  await expect(page.getByRole('heading', { name: 'Set up P42cloud' })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);
  await noSidewaysScroll(page);

  await page.getByRole('textbox', { name: 'Setup code' }).fill('E2ESETUP');
  await next();
  await page.getByRole('textbox', { name: 'Name' }).first().fill('Acme');
  await next();
  await page.getByRole('textbox', { name: 'Name' }).nth(1).fill('Ada Admin');
  await page.getByRole('textbox', { name: 'Email' }).fill('ada@acme.test');
  await page
    .getByRole('textbox', { name: 'Password', exact: true })
    .fill('correct horse battery staple');
  await page
    .getByRole('textbox', { name: 'Repeat the password' })
    .fill('correct horse battery staple');
  await next();
  await expect(page.getByText('always included')).toBeVisible(); // the core
  // A test installation's image brings no release address: it looks for no updates.
  const release = page.getByRole('textbox', { name: 'Release address' });
  await expect(release).toHaveValue('');
  await expect(page.getByText('never looks for updates')).toBeVisible();
  await release.fill('ftp://nowhere');
  await next();
  const folder = page.getByRole('textbox', { name: 'Backup folder' });
  await folder.fill('/srv/p42-backups');
  await expect(page.getByText('once deploy/restart.sh has run')).toBeVisible();
  const finish = page.getByRole('button', { name: 'Set up', exact: true });
  await page.getByText('I’ll set up backups later').click();
  await expect(finish).toBeDisabled(); // the master key's copy is confirmed first
  await page.getByText('I have stored a copy of the master key').click();
  await finish.click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'releases/download/registry' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Back' }).filter({ visible: true }).last().click();
  await release.fill('');
  await next();
  await finish.click();

  await expect(page.getByRole('heading', { name: 'Set up a second factor' })).toBeVisible();
  await page.getByRole('button', { name: 'Use an authenticator app' }).click();
  const key = (await page.locator('.key code').textContent())!.trim();
  await page.getByLabel('Code').fill(code(key));
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByLabel('I’ve saved my recovery codes').check();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: 'Hello, Ada Admin' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Acme' }).first()).toBeVisible(); // its name

  await page.goto('/setup');
  await expect(page.getByText('This installation is set up already.')).toBeVisible();
});
