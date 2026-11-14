import { Injectable, inject } from '@angular/core';

import { Api } from './api';
import { Condition, NO_VALUE } from './filters';

/** A module's list view, as `sys.list_view` holds it. */
export interface ListView {
  name: string;
  table: string;
  label: unknown;
  columns: string[];
  sort: string[] | null;
  filter: unknown;
  available: string[] | null;
  search: string[] | null;
}

/** A person's saved view of a list. */
export interface SavedView {
  sys_id: string;
  _etag: string;
  view: string;
  name: string;
  columns: string[] | null;
  sort: string[] | null;
  filter: unknown;
  search: string | null;
  is_default: boolean;
}

/** The columns and sort a person last used on a list. */
export interface Remembered {
  sys_id: string;
  _etag: string;
  columns: string[] | null;
  sort: string[] | null;
}

const SAVED = '/api/v1/sys/saved_view';
const STATE = '/api/v1/sys/view_state';

/** Saved views back into conditions: they're stored in the filter language. */
export function conditionsOf(where: unknown): Condition[] {
  if (!where || typeof where !== 'object') {
    return [];
  }
  const node = where as Record<string, unknown>;
  const leaves = Array.isArray(node['and']) ? (node['and'] as unknown[]) : [node];
  return leaves
    .filter((l): l is Record<string, unknown> => !!l && typeof l === 'object' && 'field' in l)
    .map((l) => ({
      field: String(l['field']),
      op: String(l['op'] ?? 'eq'),
      ...(NO_VALUE.has(String(l['op'])) ? {} : { value: l['value'] as string | string[] }),
    }));
}

/** The list views, saved views and remembered lists, through the records API. */
@Injectable({ providedIn: 'root' })
export class ListViews {
  private readonly api = inject(Api);

  async view(name: string): Promise<ListView | null> {
    const { data } = await this.api.get<{ records: ListView[] }>('/api/v1/sys/list_view', {
      query: { 'filter[name]': name, count: false },
    });
    return data.records[0] ?? null;
  }

  async saved(view: string): Promise<SavedView[]> {
    const { data } = await this.api.get<{ records: SavedView[] }>(SAVED, {
      query: { 'filter[view]': view, sort: 'name', limit: 100, count: false },
    });
    return data.records;
  }

  async remembered(view: string): Promise<Remembered | null> {
    const { data } = await this.api.get<{ records: Remembered[] }>(STATE, {
      query: { 'filter[view]': view, limit: 1, count: false },
    });
    return data.records[0] ?? null;
  }

  async remember(
    view: string,
    found: Remembered | null,
    values: Partial<Remembered>,
  ): Promise<Remembered> {
    const answer = found
      ? await this.api.patch<Remembered>(`${STATE}/${found.sys_id}`, values, found._etag)
      : await this.api.post<Remembered>(STATE, { view, ...values });
    return answer.data;
  }

  async forget(found: Remembered): Promise<void> {
    await this.api.delete(`${STATE}/${found.sys_id}`, found._etag);
  }

  async save(view: string, values: Partial<SavedView>): Promise<SavedView> {
    return (await this.api.post<SavedView>(SAVED, { view, ...values })).data;
  }

  async change(saved: SavedView, values: Partial<SavedView>): Promise<SavedView> {
    return (await this.api.patch<SavedView>(`${SAVED}/${saved.sys_id}`, values, saved._etag)).data;
  }

  async remove(saved: SavedView): Promise<void> {
    await this.api.delete(`${SAVED}/${saved.sys_id}`, saved._etag);
  }
}
