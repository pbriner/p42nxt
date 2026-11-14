import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { Locator, Page, TestInfo, expect } from '@playwright/test';

export interface Person {
  name: string;
  email: string;
  password: string;
  key: string;
}

interface State {
  url: string;
  people: Record<string, Person>;
}

export function state(): State {
  const port = process.env['P42_E2E_PORT'] ?? '8898';
  return JSON.parse(readFileSync(join(__dirname, `.state-${port}.json`), 'utf8')) as State;
}

export function person(name: string): Person {
  return state().people[name]!;
}

export const storage = (name: string): string => join(__dirname, '.auth', `${name}.json`);

function base32(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of text.replace(/=+$/, '')) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, '0');
  }
  const bytes = bits.match(/.{8}/g) ?? [];
  return Buffer.from(bytes.map((b) => parseInt(b, 2)));
}

/** The authenticator code for now (RFC 6238: 30 seconds, 6 digits, SHA-1). */
export function code(key: string, at = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const mac = createHmac('sha1', base32(key)).update(counter).digest();
  const offset = mac[mac.length - 1]! & 0x0f;
  const number = (mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return number.toString().padStart(6, '0');
}

/** The person who signs in through the screens at this browser size. */
export function signer(info: TestInfo): Person {
  return person(`signer-${info.project.name}`);
}

/** The page may never scroll sideways. */
export async function noSidewaysScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, 'the page scrolls sideways').toBeLessThanOrEqual(0);
}

export function compact(info: TestInfo): boolean {
  return (info.project.use.viewport?.width ?? 1440) < 960;
}

/** Lists show cards below 720 pixels. */
export function cardsShown(info: TestInfo): boolean {
  return (info.project.use.viewport?.width ?? 1440) < 720;
}

/** The API as the signed-in person of this page (cookies and CSRF token). */
export async function api(page: Page): Promise<{
  get: (path: string) => Promise<any>;
  post: (path: string, data: unknown) => Promise<any>;
  remove: (path: string, etag: string) => Promise<void>;
}> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === 'p42_csrf')?.value ?? '';
  const headers = { 'X-CSRF-Token': csrf, Origin: state().url };
  return {
    get: async (path) => (await page.request.get(path, { headers })).json(),
    post: async (path, data) => {
      const answer = await page.request.post(path, { headers, data });
      expect(answer.ok(), await answer.text()).toBeTruthy();
      return answer.json();
    },
    remove: async (path, etag) => {
      await page.request.delete(path, { headers: { ...headers, 'If-Match': etag } });
    },
  };
}

/** Forgets the person's saved views and remembered columns of a list. */
export async function freshList(page: Page, view: string): Promise<void> {
  const client = await api(page);
  for (const table of ['saved_view', 'view_state']) {
    const found = await client.get(`/api/v1/sys/${table}?filter[view]=${view}&limit=100`);
    for (const record of found.records ?? []) {
      await client.remove(`/api/v1/sys/${table}/${record.sys_id}`, record._etag);
    }
  }
}

/** The dialog once it has finished opening: taps during its animation are lost. */
export async function opened(page: Page): Promise<Locator> {
  await expect(page.locator('.mat-mdc-dialog-container.mdc-dialog--open')).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.getAnimations().length)).toBe(0);
  return page.getByRole('dialog');
}

/** Picks an option of a select; a click while a dialog settles may not open it, so retry. */
export async function choose(page: Page, select: Locator, option: string): Promise<void> {
  await expect(async () => {
    await select.click();
    await expect(page.getByRole('listbox')).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 10_000 });
  await page.getByRole('option', { name: option, exact: true }).click();
}
