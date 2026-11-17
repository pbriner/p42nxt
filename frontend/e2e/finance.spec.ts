import { expect, test } from '@playwright/test';

import { api, noSidewaysScroll, storage } from './helpers';

test.use({ storageState: storage('admin') });

test('a running-cost statement is worked out and made final', async ({ page }, info) => {
  const tag = `${info.project.name}-${Date.now() % 100000}`;
  await page.goto('/');
  const client = await api(page);
  const center = async (name: string, more: Record<string, unknown> = {}) =>
    (await client.post('/api/v1/costing/center', { code: `${name}-${tag}`, name, ...more }))
      .sys_id as string;
  const house = await center('House', { kind: 'profit' });
  const flat = await center('Flat', { parent: house });
  const water = (
    await client.post('/api/v1/costing/cost_type', {
      code: `W-${tag}`,
      name: `Water ${tag}`,
      chargeable: true,
    })
  ).sys_id;
  const tenant = (
    await client.post('/api/v1/partners/partner', { name: `Tenant ${tag}`, tenant: true })
  ).sys_id;
  const lease = (
    await client.post('/api/v1/partners/contract', {
      partner: tenant,
      name: `Lease ${tag}`,
      kind: 'lease',
      advance: '50',
    })
  ).sys_id;
  const rental = (await client.post('/api/v1/finance/rental', { contract: lease, center: flat }))
    .sys_id;
  await client.post('/api/v1/finance/booking', {
    date: '2026-03-10',
    text: 'Water bill',
    cost_type: water,
    center: flat,
    amount: '230',
  });
  const statement = (
    await client.post('/api/v1/finance/statement', {
      rental,
      start: '2026-01-01',
      end: '2026-03-31',
    })
  ).sys_id;

  await page.goto(`/form/finance.statement/${statement}`);
  await page.getByRole('button', { name: 'Work out' }).click();
  // 230 of water less 3 months of 50 in advances.
  await expect(page.getByText('Worked out: CHF 80.00 to pay.')).toBeVisible();
  await page.getByRole('button', { name: 'Make final' }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /Make final|Confirm|OK|Yes/ })
    .click();
  await expect(page.getByRole('button', { name: 'Work out' })).toHaveCount(0);
  await noSidewaysScroll(page);
});
