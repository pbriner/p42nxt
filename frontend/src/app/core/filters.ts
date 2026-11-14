import { ParamMap } from '@angular/router';

import { TextKey } from '../i18n/en';
import { FieldInfo } from './format';

/** One condition of a list's filter; a list's conditions all apply (AND). */
export interface Condition {
  field: string;
  op: string;
  value?: string | string[];
}

const LISTS = new Set(['in', 'not_in', 'between']);
export const NO_VALUE = new Set(['empty', 'not_empty']);
const PARAM = /^filter\[([a-z0-9_.]+)\](?:\[([a-z_]+)\])?$/;

/** The conditions in an address: `filter[state][in]=open,done`, the API's own form. */
export function fromParams(params: ParamMap): Condition[] {
  const found: Condition[] = [];
  for (const key of params.keys) {
    const match = PARAM.exec(key);
    if (!match) {
      continue;
    }
    for (const raw of params.getAll(key)) {
      const op = match[2] ?? 'eq';
      const condition: Condition = { field: match[1]!, op };
      if (!NO_VALUE.has(op)) {
        condition.value = LISTS.has(op) ? raw.split(',') : raw;
      }
      found.push(condition);
    }
  }
  return found;
}

/** Conditions as query parameters; null clears those no longer there. */
export function toParams(
  conditions: Condition[],
  before: ParamMap | null,
): Record<string, string | string[] | null> {
  const params: Record<string, string | string[] | null> = {};
  for (const key of before?.keys ?? []) {
    if (PARAM.test(key)) {
      params[key] = null;
    }
  }
  const grouped = new Map<string, string[]>();
  for (const c of conditions) {
    const key = `filter[${c.field}][${c.op}]`;
    const value = Array.isArray(c.value) ? c.value.join(',') : (c.value ?? '');
    grouped.set(key, [...(grouped.get(key) ?? []), value]);
  }
  for (const [key, values] of grouped) {
    params[key] = values.length === 1 ? values[0]! : values;
  }
  return params;
}

/** The filter language for the API: one condition, or all of them. */
export function toWhere(conditions: Condition[]): unknown {
  const leaves = conditions.map((c) => ({
    field: c.field,
    op: c.op,
    ...(NO_VALUE.has(c.op) ? {} : { value: c.value }),
  }));
  return leaves.length === 0 ? null : leaves.length === 1 ? leaves[0] : { and: leaves };
}

/** Which conditions people can build for a kind of field, in the order offered. */
export function operators(field: FieldInfo): string[] {
  switch (field.kind) {
    case 'text':
      return ['contains', 'eq', 'ne', 'starts_with', 'empty', 'not_empty'];
    case 'translated':
      return ['contains', 'starts_with', 'empty', 'not_empty'];
    case 'number':
    case 'date':
    case 'datetime':
      return ['eq', 'lt', 'gt', 'between', 'empty', 'not_empty'];
    case 'choice':
      return ['in', 'not_in', 'empty', 'not_empty'];
    case 'reference':
      return ['eq', 'empty', 'not_empty'];
    case 'boolean':
      return ['eq'];
    default:
      return [];
  }
}

/** The text for an operator, which depends on the kind of field ("before" for dates). */
export function operatorKey(op: string, kind: string | null): TextKey {
  const dated = kind === 'date' || kind === 'datetime';
  const numeric = kind === 'number';
  switch (op) {
    case 'lt':
      return dated ? 'op.before' : 'op.less';
    case 'gt':
      return dated ? 'op.after' : 'op.more';
    case 'lte':
      return dated ? 'op.on_or_before' : 'op.at_most';
    case 'gte':
      return dated ? 'op.on_or_after' : 'op.at_least';
    case 'eq':
      return dated ? 'op.on' : 'op.is';
    case 'ne':
      return 'op.is_not';
    case 'in':
      return 'op.is_one_of';
    case 'not_in':
      return 'op.is_not_one_of';
    case 'contains':
      return 'op.contains';
    case 'starts_with':
      return 'op.starts_with';
    case 'between':
      return numeric || dated ? 'op.between' : 'op.is';
    case 'empty':
      return 'op.empty';
    case 'not_empty':
      return 'op.not_empty';
    default:
      return 'op.is';
  }
}

/** Relative dates the filter offers, as the filter language writes them. */
export const RELATIVE_DATES = [
  '@today',
  '@today-1d',
  '@today-7d',
  '@today-30d',
  '@start_of_week',
  '@start_of_month',
  '@start_of_quarter',
  '@start_of_year',
] as const;

const RELATIVE = /^@(today|now)(?:([+-])(\d{1,4})([dwmyh]))?$/;
const PERIOD = /^@(start|end)_of_(week|month|quarter|year)$/;
const UNITS: Record<string, TextKey> = {
  d: 'date.days',
  w: 'date.weeks',
  m: 'date.months',
  y: 'date.years',
  h: 'date.hours',
};

type Translate = (key: TextKey, params?: Record<string, string | number>) => string;

/** A relative date in words ("30 days ago"), or null if it isn't one. */
export function relative(value: string, t: Translate): string | null {
  const period = PERIOD.exec(value);
  if (period) {
    return t(`date.${period[1]}_of_${period[2]}` as TextKey);
  }
  const found = RELATIVE.exec(value);
  if (!found) {
    return null;
  }
  if (!found[2]) {
    return t(found[1] === 'now' ? 'date.now' : 'date.today');
  }
  const amount = `${found[3]} ${t(UNITS[found[4]!]!)}`;
  return t(found[2] === '-' ? 'date.ago' : 'date.in', { amount });
}
