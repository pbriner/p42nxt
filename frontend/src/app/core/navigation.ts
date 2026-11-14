import { Injectable, computed, inject, signal } from '@angular/core';

import { Api } from './api';
import { Condition } from './filters';

export interface NavItem {
  name: string;
  label: string;
  icon: string | null;
  kind: 'list' | 'create' | 'page';
  table: string | null;
  view: string | null;
  filter: unknown;
  path: string | null;
  keywords: string[];
}

export interface NavSection {
  label: string | null;
  items: NavItem[];
}

export interface NavModule {
  id: string;
  name: string;
  icon: string | null;
  sections: NavSection[];
}

export interface NavCategory {
  name: string;
  modules: NavModule[];
}

export interface Found {
  item: NavItem;
  module: NavModule;
}

/** The navigator as the server gives it: only what the person may open. */
@Injectable({ providedIn: 'root' })
export class Navigation {
  private readonly api = inject(Api);
  readonly categories = signal<NavCategory[]>([]);
  readonly loaded = signal(false);
  readonly items = computed<Found[]>(() =>
    this.categories().flatMap((c) =>
      c.modules.flatMap((module) =>
        module.sections.flatMap((s) => s.items.map((item) => ({ item, module }))),
      ),
    ),
  );

  async load(): Promise<void> {
    const { data } = await this.api.get<{ categories: NavCategory[] }>('/api/v1/navigation');
    this.categories.set(data.categories);
    this.loaded.set(true);
  }

  find(name: string): Found | undefined {
    return this.items().find((f) => f.item.name === name);
  }

  /** Items whose label, module or keywords contain the text (case and accents ignored). */
  matching(text: string): Found[] {
    const wanted = fold(text);
    return this.items().filter(
      ({ item, module }) =>
        fold(item.label).includes(wanted) ||
        fold(module.name).includes(wanted) ||
        item.keywords.some((k) => fold(k).includes(wanted)),
    );
  }
}

export function fold(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

/** Where an item leads, as router commands and query parameters. */
export function target(item: NavItem): { commands: string[]; query: Record<string, string> } {
  if (item.kind === 'page' && item.path) {
    return { commands: [item.path], query: {} };
  }
  const list = item.view ?? item.table ?? '';
  if (item.kind === 'create') {
    return { commands: ['/form', item.table ?? '', 'new'], query: {} };
  }
  return { commands: ['/list', list], query: filterQuery(item.filter) };
}

/** A filter as list parameters: plain conditions as chips, anything else as a fixed filter. */
function filterQuery(filter: unknown): Record<string, string> {
  if (!filter || typeof filter !== 'object') {
    return {};
  }
  const node = filter as Record<string, unknown>;
  const leaves = Array.isArray(node['and']) ? (node['and'] as unknown[]) : [node];
  const plain = leaves.every(
    (l) =>
      !!l &&
      typeof l === 'object' &&
      'field' in l &&
      !('and' in l) &&
      !('or' in l) &&
      !('not' in l),
  );
  if (!plain) {
    return { where: JSON.stringify(filter) };
  }
  const query: Record<string, string> = {};
  for (const leaf of leaves as Condition[]) {
    const value = Array.isArray(leaf.value) ? leaf.value.join(',') : (leaf.value ?? '');
    query[`filter[${leaf.field}][${leaf.op ?? 'eq'}]`] = value;
  }
  return query;
}
