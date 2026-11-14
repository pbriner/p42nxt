import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatTabsModule } from '@angular/material/tabs';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api } from '../core/api';
import { FieldInfo, TableInfo, shown } from '../core/format';
import { I18n } from '../core/i18n';
import { ListViews } from '../core/list-views';
import { Tables, path } from '../core/tables';
import { RelatedListsDialog } from './related-lists-dialog';

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
const LAYOUTS = '/api/v1/sys/related_layout';

/** The person's own choice for a form (a personal record). */
interface Layout {
  sys_id: string;
  _etag: string;
  shown: { table: string; field: string }[] | null;
  last: string | null;
}

/** The records pointing to this one, a tab per kind, each with its count. */
@Component({
  selector: 'app-related-tabs',
  imports: [MatButtonModule, MatTabsModule, RouterLink],
  templateUrl: './related-tabs.html',
  styleUrl: './related-tabs.scss',
})
export class RelatedTabs {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  private readonly views = inject(ListViews);
  private readonly router = inject(Router);
  private readonly dialog = inject(MatDialog);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  readonly lists = input.required<Related[]>();

  protected readonly counts = signal(new Map<string, number | null>());
  private readonly layout = signal<Layout | null>(null);
  protected readonly selected = signal(0);
  protected readonly opened = signal<Opened | null>(null);

  /** The person's chosen lists in their order; else the default: declared ones, then those with
   * records. Lists they may not read aren't in `lists`, so they never show. */
  protected readonly tabs = computed(() => {
    const chosen = this.layout()?.shown;
    if (chosen) {
      const byKey = new Map(this.lists().map((r) => [this.key(r), r]));
      return chosen.map((c) => byKey.get(`${c.table}.${c.field}`)).filter((r): r is Related => !!r);
    }
    return this.lists().filter((r) => r.declared || (this.counts().get(this.key(r)) ?? 0) > 0);
  });

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
    await this.loadLayout();
    const last = this.layout()?.last;
    this.choose(
      Math.max(
        0,
        this.tabs().findIndex((r) => this.key(r) === last),
      ),
      false,
    );
  }

  private async loadLayout(): Promise<void> {
    try {
      const { data } = await this.api.get<{ records: Layout[] }>(LAYOUTS, {
        query: { 'filter[form]': this.table(), limit: 1, count: false },
      });
      this.layout.set(data.records[0] ?? null);
    } catch {
      this.layout.set(null);
    }
  }

  /** Keeps the person's layout: changes theirs, or makes it. */
  private async keep(values: Partial<Layout>): Promise<void> {
    const found = this.layout();
    try {
      const answer = found
        ? await this.api.patch<Layout>(`${LAYOUTS}/${found.sys_id}`, values, found._etag)
        : await this.api.post<Layout>(LAYOUTS, { form: this.table(), ...values });
      this.layout.set(answer.data);
    } catch {
      // Not kept this time; the screen shows it all the same.
    }
  }

  protected choose(index: number, keepIt = true): void {
    this.selected.set(index);
    const related = this.tabs()[index];
    if (!related) {
      this.opened.set(null);
      return;
    }
    if (keepIt && this.layout()?.last !== this.key(related)) {
      void this.keep({ last: this.key(related) });
    }
    void this.open(related);
  }

  /** The slushbucket: which lists, in what order, for this person. */
  protected async arrange(): Promise<void> {
    const items = this.lists().map((r) => ({
      key: this.key(r),
      label: r.label,
      detail: this.count(r),
    }));
    const ref = this.dialog.open(RelatedListsDialog, {
      data: { items, shown: this.tabs().map((r) => this.key(r)) },
      maxWidth: '96vw',
    });
    const answer = await firstValueFrom(ref.afterClosed());
    if (answer === 'reset') {
      const found = this.layout();
      if (found) {
        await this.api.delete(`${LAYOUTS}/${found.sys_id}`, found._etag).catch(() => undefined);
      }
      this.layout.set(null);
    } else if (Array.isArray(answer)) {
      const byKey = new Map(this.lists().map((r) => [this.key(r), r]));
      const shown = answer
        .map((k) => byKey.get(k))
        .filter((r): r is Related => !!r)
        .map((r) => ({ table: r.table, field: r.field }));
      await this.keep({ shown });
    } else {
      return;
    }
    this.choose(0, false);
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
