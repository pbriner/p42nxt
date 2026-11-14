import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { Page, TestInfo, expect } from '@playwright/test';

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
