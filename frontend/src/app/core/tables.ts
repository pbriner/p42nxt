import { Injectable, inject } from '@angular/core';

import { Api } from './api';
import { TableInfo, textIn } from './format';

/** `sys.person` → `/api/v1/sys/person`. */
export function path(table: string, id?: string): string {
  const [module, name] = table.split('.');
  return `/api/v1/${module}/${name}${id ? `/${id}` : ''}`;
}

export interface Named {
  sys_id: string;
  name: string;
}

/** Tables as described for the person, and the names of their records. */
@Injectable({ providedIn: 'root' })
export class Tables {
  private readonly api = inject(Api);
  private readonly described = new Map<string, Promise<TableInfo>>();

  describe(table: string): Promise<TableInfo> {
    const key = `${table}:${this.api.language()}`;
    let found = this.described.get(key);
    if (!found) {
      const [module, name] = table.split('.');
      found = this.api.get<TableInfo>(`/api/v1/meta/${module}/${name}`).then((a) => a.data);
      found.catch(() => this.described.delete(key));
      this.described.set(key, found);
    }
    return found;
  }

  /** The display names of some records (those the person may read). */
  async names(table: string, ids: string[]): Promise<Map<string, string>> {
    const found = new Map<string, string>();
    if (!ids.length) {
      return found;
    }
    const info = await this.describe(table);
    const display = info.display ?? 'sys_id';
    const { data } = await this.api.get<{ records: Record<string, unknown>[] }>(path(table), {
      query: {
        'filter[sys_id][in]': ids.join(','),
        fields: display,
        limit: Math.min(ids.length, 500),
        count: false,
      },
    });
    for (const record of data.records) {
      found.set(String(record['sys_id']), String(record[display] ?? record['sys_id']));
    }
    return found;
  }

  /** Records whose display field contains the text: for pickers. */
  /** One record's name (its display field); null when it can't be read (gone, or not allowed). */
  async nameOf(table: string, id: string): Promise<string | null> {
    try {
      const info = await this.describe(table);
      const display = info.display ?? 'sys_id';
      const { data } = await this.api.get<Record<string, unknown>>(`${path(table)}/${id}`, {
        query: { fields: display },
        quiet: true,
      });
      const name = this.shownName(data, display);
      return name === '' ? null : name;
    } catch {
      return null;
    }
  }

  async find(
    table: string,
    text: string,
    limit = 10,
    narrow: Record<string, string> = {},
  ): Promise<Named[]> {
    const info = await this.describe(table);
    const display = info.display ?? 'sys_id';
    const { data } = await this.api.get<{ records: Record<string, unknown>[] }>(path(table), {
      query: {
        ...narrow,
        [`filter[${display}][contains]`]: text || null,
        fields: display,
        sort: display,
        limit,
        count: false,
      },
    });
    return data.records.map((r) => ({
      sys_id: String(r['sys_id']),
      name: this.shownName(r, display) || String(r['sys_id']),
    }));
  }

  /** How a record is named: as the API shows it (a hierarchy's records by their path, like
   *  "House › Heating"), else its display field. */
  private shownName(record: Record<string, unknown>, display: string): string {
    const shown = (record['_display'] as Record<string, unknown> | undefined)?.[display];
    return shown !== undefined && shown !== null ? String(shown) : this.named(record[display]);
  }

  /** A display value as text: a translated one in the person's language. */
  private named(value: unknown): string {
    if (value === null || value === undefined) {
      return '';
    }
    return typeof value === 'object' ? textIn(value, this.api.language()) : String(value);
  }
}
