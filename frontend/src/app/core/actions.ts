import { Injectable, inject } from '@angular/core';

import { Api } from './api';
import { path } from './tables';

export interface ActionInputDef {
  name: string;
  label: unknown;
  type: string;
  required: boolean;
  options: Record<string, unknown>;
}

/** A UI action as `sys.ui_action` holds it. */
export interface ActionDef {
  name: string;
  table: string;
  label: unknown;
  icon: string | null;
  places: string[];
  confirm: unknown;
  inputs: ActionInputDef[] | null;
}

export interface ActionAnswer {
  sys_id: string;
  message: string | null;
  go: 'stay' | 'list';
}

export interface ManyAnswer {
  results: (ActionAnswer & { ok: boolean; title?: string; detail?: string | null })[];
}

/** The actions of a table, and running them; whether one applies comes with each record. */
@Injectable({ providedIn: 'root' })
export class Actions {
  private readonly api = inject(Api);
  private readonly known = new Map<string, Promise<ActionDef[]>>();

  of(table: string): Promise<ActionDef[]> {
    let found = this.known.get(table);
    if (!found) {
      found = this.api
        .get<{ records: ActionDef[] }>('/api/v1/sys/ui_action', {
          query: { 'filter[table]': table, sort: 'name', limit: 100, count: false },
        })
        .then((a) => a.data.records)
        .catch(() => []);
      this.known.set(table, found);
    }
    return found;
  }

  async run(
    table: string,
    id: string,
    name: string,
    inputs: Record<string, unknown>,
    etag?: string,
  ): Promise<ActionAnswer> {
    const { data } = await this.api.request<ActionAnswer>(
      'POST',
      `${path(table, id)}/action/${name}`,
      {
        body: { inputs },
        ifMatch: etag,
      },
    );
    return data;
  }

  async runMany(
    table: string,
    ids: string[],
    name: string,
    inputs: Record<string, unknown>,
  ): Promise<ManyAnswer> {
    const { data } = await this.api.post<ManyAnswer>(`${path(table)}/action/${name}`, {
      ids,
      inputs,
    });
    return data;
  }
}
