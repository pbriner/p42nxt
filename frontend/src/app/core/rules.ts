import { Injectable, inject } from '@angular/core';

import { Api } from './api';

/** A form rule as `sys.form_rule` holds it. */
export interface FormRule {
  condition: unknown;
  hide: string[] | null;
  required: string[] | null;
  read_only: string[] | null;
}

export interface Effects {
  hidden: Set<string>;
  required: Set<string>;
  readOnly: Set<string>;
}

type Values = Record<string, unknown>;

function empty(v: unknown): boolean {
  return v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0);
}

function value(v: unknown, me: string): unknown {
  return v === '@me' ? me : v;
}

function compare(a: unknown, b: unknown): number {
  const x = Number(a);
  const y = Number(b);
  if (!Number.isNaN(x) && !Number.isNaN(y) && a !== '' && b !== '') {
    return x - y;
  }
  return String(a).localeCompare(String(b));
}

function text(v: unknown): string {
  return String(v ?? '').toLowerCase();
}

/** The part of the filter language rules use, as the server evaluates it (the server checks
 * the same rules again on saving). */
export function holds(node: unknown, values: Values, me: string): boolean {
  if (node === null || node === undefined) {
    return true;
  }
  const n = node as Record<string, unknown>;
  if (Array.isArray(n['and'])) {
    return (n['and'] as unknown[]).every((p) => holds(p, values, me));
  }
  if (Array.isArray(n['or'])) {
    return (n['or'] as unknown[]).some((p) => holds(p, values, me));
  }
  if ('not' in n) {
    return !holds(n['not'], values, me);
  }
  const got = values[String(n['field'])];
  const want = Array.isArray(n['value'])
    ? (n['value'] as unknown[]).map((v) => value(v, me))
    : value(n['value'], me);
  const list = Array.isArray(want) ? want : [want];
  switch (n['op'] ?? 'eq') {
    case 'eq':
      return !empty(got) && String(got) === String(want);
    case 'ne':
      return empty(got) || String(got) !== String(want);
    case 'in':
      return !empty(got) && list.map(String).includes(String(got));
    case 'not_in':
      return empty(got) || !list.map(String).includes(String(got));
    case 'empty':
      return empty(got);
    case 'not_empty':
      return !empty(got);
    case 'contains':
      return text(got).includes(text(want));
    case 'not_contains':
      return empty(got) || !text(got).includes(text(want));
    case 'starts_with':
      return text(got).startsWith(text(want));
    case 'ends_with':
      return text(got).endsWith(text(want));
    case 'lt':
      return !empty(got) && compare(got, want) < 0;
    case 'lte':
      return !empty(got) && compare(got, want) <= 0;
    case 'gt':
      return !empty(got) && compare(got, want) > 0;
    case 'gte':
      return !empty(got) && compare(got, want) >= 0;
    case 'between':
      return !empty(got) && compare(got, list[0]) >= 0 && compare(got, list[1]) <= 0;
    default:
      return false;
  }
}

export function effects(rules: FormRule[], values: Values, me: string): Effects {
  const found: Effects = { hidden: new Set(), required: new Set(), readOnly: new Set() };
  for (const rule of rules) {
    if (!holds(rule.condition, values, me)) {
      continue;
    }
    (rule.hide ?? []).forEach((f) => found.hidden.add(f));
    (rule.required ?? []).forEach((f) => found.required.add(f));
    (rule.read_only ?? []).forEach((f) => found.readOnly.add(f));
  }
  return found;
}

@Injectable({ providedIn: 'root' })
export class Rules {
  private readonly api = inject(Api);

  async of(table: string): Promise<FormRule[]> {
    try {
      const { data } = await this.api.get<{ records: FormRule[] }>('/api/v1/sys/form_rule', {
        query: { 'filter[table]': table, limit: 200, count: false },
      });
      return data.records;
    } catch {
      return [];
    }
  }
}
