import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatTabsModule } from '@angular/material/tabs';
import { Router, RouterLink } from '@angular/router';

import { Api } from '../core/api';
import { FieldInfo, TableInfo, shown } from '../core/format';
import { I18n } from '../core/i18n';
import { ListViews } from '../core/list-views';
import { remember, remembered } from '../core/storage';
import { Tables, path } from '../core/tables';

export interface Related {
  table: string;
  field: string;
  label: string;
  /** Declared by the form: always a tab, even when empty. */
  declared: boolean;
}

interface Opened {
  related: Related;
  info: TableInfo;
  columns: string[];
  rows: Record<string, unknown>[];
}

const MAX_ROWS = 20;

/** The records pointing to this one, a tab per kind, each with its count. */
@Component({
  selector: 'app-related-tabs',
  imports: [MatButtonModule, MatMenuModule, MatTabsModule, RouterLink],
  templateUrl: './related-tabs.html',
  styleUrl: './related-tabs.scss',
})
export class RelatedTabs {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  private readonly views = inject(ListViews);
  private readonly router = inject(Router);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  readonly lists = input.required<Related[]>();

  protected readonly counts = signal(new Map<string, number | null>());
  protected readonly added = signal(new Set<string>());
  protected readonly selected = signal(0);
  protected readonly opened = signal<Opened | null>(null);

  protected readonly tabs = computed(() =>
    this.lists().filter(
      (r) =>
        r.declared || (this.counts().get(this.key(r)) ?? 0) > 0 || this.added().has(this.key(r)),
    ),
  );
  protected readonly more = computed(() => this.lists().filter((r) => !this.tabs().includes(r)));

  constructor() {
    effect(() => {
      const lists = this.lists();
      const id = this.id();
      void untracked(() => this.countAll(lists, id));
    });
  }

  protected key(r: Related): string {
    return `${r.table}.${r.field}`;
  }

  private async countAll(lists: Related[], id: string): Promise<void> {
    const counts = new Map<string, number | null>();
    await Promise.all(
      lists.map(async (r) => {
        try {
          const { data } = await this.api.get<{ total: number | null }>(path(r.table), {
            query: { [`filter[${r.field}]`]: id, limit: 1, fields: 'sys_id', count: true },
          });
          counts.set(this.key(r), data.total);
        } catch {
          counts.set(this.key(r), null);
        }
      }),
    );
    this.counts.set(counts);
    const last = remembered(`p42.related.${this.table()}`)[0];
    const at = Math.max(
      0,
      this.tabs().findIndex((r) => this.key(r) === last),
    );
    this.choose(at);
  }

  protected choose(index: number): void {
    this.selected.set(index);
    const related = this.tabs()[index];
    if (related) {
      remember(`p42.related.${this.table()}`, [this.key(related)]);
      void this.open(related);
    }
  }

  protected addTab(related: Related): void {
    this.added.update((all) => new Set([...all, this.key(related)]));
    this.choose(this.tabs().findIndex((r) => this.key(r) === this.key(related)));
  }

  private async open(related: Related): Promise<void> {
    const [info, view] = await Promise.all([
      this.tables.describe(related.table),
      this.views.view(related.table).catch(() => null),
    ]);
    const columns = (view?.columns ?? [info.display ?? 'sys_id']).filter(
      (c) => c !== related.field && info.fields.find((f) => f.name === c)?.read,
    );
    const { data } = await this.api.get<{ records: Record<string, unknown>[] }>(
      path(related.table),
      {
        query: {
          [`filter[${related.field}]`]: this.id(),
          limit: MAX_ROWS,
          sort: view?.sort?.join(',') ?? '-updated',
          count: false,
        },
      },
    );
    this.opened.set({ related, info, columns, rows: data.records });
  }

  protected field(info: TableInfo, name: string): FieldInfo | undefined {
    return info.fields.find((f) => f.name === name);
  }

  protected value(info: TableInfo, row: Record<string, unknown>, name: string): string {
    return shown(row, this.field(info, name), name, this.i18n.language(), (k) => this.i18n.t(k));
  }

  /** A new record that already points back here. */
  protected prefill(related: Related): Record<string, string> {
    return { [related.field]: this.id() };
  }

  protected openRow(table: string, row: Record<string, unknown>): void {
    void this.router.navigate(['/form', table, String(row['sys_id'])]);
  }

  protected count(r: Related): string {
    const n = this.counts().get(this.key(r));
    return n === undefined ? '·' : n === null ? '?' : String(n);
  }
}
