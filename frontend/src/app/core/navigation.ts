import { Injectable, computed, inject, signal } from '@angular/core';

import { Api } from './api';

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
  return {
    commands: ['/list', list],
    query: item.filter ? { where: JSON.stringify(item.filter) } : {},
  };
}
