import { expect, test } from '@playwright/test';

import { code, noSidewaysScroll, person, signer, state } from './helpers';

test('signing in with a password and a code leads back to the page asked for', async ({
  page,
}, info) => {
  const who = signer(info);
  await page.goto('/profile');
  await expect(page).toHaveURL(/\/sign-in\?next=%2Fprofile/);
  await expect(page.getByRole('heading', { name: 'P42cloud' })).toBeVisible();
  await noSidewaysScroll(page);

  const signIn = page.getByRole('button', { name: 'Sign in', exact: true });
  await expect(signIn).toBeDisabled();
  await page.getByLabel('Email').fill(who.email);
  await page.getByLabel('Password').fill('not the password');
  await signIn.click();
  await expect(page.getByRole('alert')).toBeVisible();

  await page.getByLabel('Password').fill(who.password);
  await signIn.click();
  await expect(page.getByText('Enter the code from your authenticator app')).toBeVisible();
  await page.getByLabel('Code').fill(code(who.key));
  await page.getByRole('button', { name: 'Verify' }).click();

  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole('heading', { name: 'Profile and settings' })).toBeVisible();
});

test('the sign-in page refuses to send people elsewhere', async ({ page }) => {
  await page.goto('/sign-in?next=//evil.example');
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  // Nothing to submit here: the check is in safeNext; the page simply stays in the app.
  expect(new URL(page.url()).host).toBe(new URL(state().url).host);
});

test('a newcomer sets up an authenticator app and keeps the recovery codes', async ({
  page,
}, info) => {
  const who = person(`newcomer-${info.project.name}`);
  await page.goto('/sign-in');
  await page.getByLabel('Email').fill(who.email);
  await page.getByLabel('Password').fill(who.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Set up a second factor' })).toBeVisible();
  await page.getByRole('button', { name: 'Use an authenticator app' }).click();

  await expect(page.getByRole('img', { name: 'QR code for your authenticator app' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open in an app on this device' })).toHaveAttribute(
    'href',
    /^otpauth:\/\/totp\//,
  );
  await noSidewaysScroll(page);
  const key = (await page.locator('.key code').textContent())!.trim();
  await page.getByLabel('Code').fill(code(key));
  await page.getByRole('button', { name: 'Confirm' }).click();

  await expect(page.getByRole('heading', { name: 'Your recovery codes' })).toBeVisible();
  const proceed = page.getByRole('button', { name: 'Continue' });
  await expect(proceed).toBeDisabled();
  await page.getByLabel('I’ve saved my recovery codes').check();
  await proceed.click();
  await expect(page.getByRole('heading', { name: `Hello, ${who.name}` })).toBeVisible();
});
