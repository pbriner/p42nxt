import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { storage } from './helpers';

const PAGES = [
  '/',
  '/profile',
  '/search?q=Ada',
  '/list/sys.person',
  '/form/sys.group/new',
  '/form/hello.greeting/new',
  '/dashboard/hello',
  '/list/sys.connection',
  '/list/sys.schedule',
];

test.describe('signed in', () => {
  test.use({ storageState: storage('admin') });
  for (const path of PAGES) {
    test(`${path} meets WCAG 2.2 AA`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState('networkidle');
      const found = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze();
      expect(
        found.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`),
      ).toEqual([]);
    });
  }
});

test('the sign-in page meets WCAG 2.2 AA', async ({ page }) => {
  await page.goto('/sign-in');
  await page.waitForLoadState('networkidle');
  const found = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
    .analyze();
  expect(found.violations.map((v) => v.id)).toEqual([]);
});
