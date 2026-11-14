import { Injectable, inject } from '@angular/core';

import { Api } from './api';
import { path } from './tables';

export interface WidgetDef {
  name: string;
  module: string;
  title: string;
  kind: 'list' | 'figure' | 'chart' | 'text';
  list: string | null;
  /** A module route answering the figure or the points (source widgets). */
  source: string | null;
  table: string | null;
  filter: unknown;
  measure: string;
  field: string | null;
  group: string | null;
  period: string | null;
  chart: 'bar' | 'donut' | 'column' | 'line' | null;
  text: string | null;
  icon: string | null;
  accent: number | null;
  filled: boolean;
  target: string | null;
  trend: string | null;
  sizes: Size[];
  size: Size;
  refresh: number | null;
  shown: 'available' | 'default' | 'mandatory';
}

export type Size = 's' | 'wide' | 'large';

export interface Placed {
  widget: string;
  size: Size;
}

export interface Board {
  dashboard: string;
  widgets: WidgetDef[];
  items: Placed[];
  new: string[];
}

export interface Group {
  key: unknown;
  label: unknown;
  value: string | number | null;
}

export interface Totals {
  total: string | number | null;
  groups: Group[];
  other: string | number | null;
}

interface Layout {
  sys_id: string;
  _etag: string;
}

const LAYOUTS = '/api/v1/sys/dashboard_layout';

/** Dashboards as the person sees them, totals for widgets, and saving an arrangement. */
@Injectable({ providedIn: 'root' })
export class Dashboards {
  private readonly api = inject(Api);

  async load(name: string): Promise<Board> {
    return (await this.api.get<Board>(`/api/v1/dashboards/${name}`)).data;
  }

  async totals(table: string, query: Record<string, string | number | null>): Promise<Totals> {
    return (await this.api.get<Totals>(`${path(table)}/aggregate`, { query })).data;
  }

  private async layout(name: string): Promise<Layout | null> {
    const { data } = await this.api.get<{ records: Layout[] }>(LAYOUTS, {
      query: { 'filter[dashboard]': name, limit: 1, count: false },
    });
    return data.records[0] ?? null;
  }

  /** The person's arrangement, and the widgets they've now seen offered. */
  async save(name: string, items: Placed[], known: string[]): Promise<void> {
    const found = await this.layout(name);
    if (found) {
      await this.api.patch(`${LAYOUTS}/${found.sys_id}`, { items, known }, found._etag);
    } else {
      await this.api.post(LAYOUTS, { dashboard: name, items, known });
    }
  }

  /** Back to the defaults: the person's arrangement goes. */
  async reset(name: string): Promise<void> {
    const found = await this.layout(name);
    if (found) {
      await this.api.delete(`${LAYOUTS}/${found.sys_id}`, found._etag);
    }
  }
}
