import { Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { ActivatedRoute, Router } from '@angular/router';

import { Api, Problem, describe } from '../core/api';
import { FieldInfo, NUMBER_TYPES, shown, textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { Preferences } from '../core/preferences';
import { ProblemState } from './problem-state';

interface ListView {
  name: string;
  table: string;
  label: unknown;
  columns: string[];
  sort: string[] | null;
  filter: unknown;
}

interface TableInfo {
  label: string;
  label_plural: string | null;
  operations: Record<string, boolean>;
  fields: FieldInfo[];
}

type Row = Record<string, unknown> & { sys_id: string };

interface Page {
  records: Row[];
  next: string | null;
  total: number | null;
  total_exact: boolean;
}

/** A list of records: the view's columns, sorted and paged. Filters and cards come next. */
@Component({
  selector: 'app-list-page',
  imports: [MatButtonModule, MatProgressBarModule, ProblemState],
  templateUrl: './list-page.html',
  styleUrl: './list-page.scss',
})
export class ListPage {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly preferences = inject(Preferences);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap);
  private readonly query = toSignal(this.route.queryParamMap);

  protected readonly view = signal<ListView | null>(null);
  protected readonly table = signal<TableInfo | null>(null);
  protected readonly page = signal<Page | null>(null);
  protected readonly busy = signal(false);
  protected readonly failed = signal<Problem | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly sort = signal<string | null>(null);
  /** The cursors of the pages before this one. */
  private readonly before = signal<(string | null)[]>([]);
  private readonly after = signal<string | null>(null);

  protected readonly fields = computed(
    () => new Map((this.table()?.fields ?? []).map((f) => [f.name, f])),
  );
  protected readonly columns = computed(() =>
    (this.view()?.columns ?? []).filter((c) => this.fields().get(c)?.read !== false),
  );
  protected readonly first = computed(
    () => this.before().length * this.preferences.values().page_size,
  );

  constructor() {
    effect(() => {
      const name = this.params()?.get('view');
      if (name) {
        void this.open(name);
      }
    });
  }

  private async open(name: string): Promise<void> {
    this.failed.set(null);
    this.view.set(null);
    this.page.set(null);
    try {
      const views = await this.api.get<{ records: ListView[] }>('/api/v1/sys/list_view', {
        query: { 'filter[name]': name, count: false },
      });
      const view = views.data.records[0];
      if (!view) {
        throw new Problem(404, 'not_found', 'Not found');
      }
      const [module, table] = view.table.split('.');
      const described = await this.api.get<TableInfo>(`/api/v1/meta/${module}/${table}`);
      this.table.set(described.data);
      this.view.set(view);
      this.sort.set(view.sort?.join(',') ?? '-updated');
      this.before.set([]);
      this.after.set(null);
      await this.load();
    } catch (error) {
      this.failed.set(
        error instanceof Problem ? error : new Problem(500, 'error', describe(error)),
      );
    }
  }

  private async load(): Promise<void> {
    const view = this.view()!;
    const [module, table] = view.table.split('.');
    const where = this.where(view);
    this.busy.set(true);
    this.error.set(null);
    try {
      const { data } = await this.api.get<Page>(`/api/v1/${module}/${table}`, {
        query: {
          limit: Math.min(this.preferences.values().page_size, 500),
          sort: this.sort(),
          after: this.after(),
          where: where ? JSON.stringify(where) : null,
        },
      });
      this.page.set(data);
    } catch (error) {
      this.error.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  /** The view's filter, the address's, and the search, all together. */
  private where(view: ListView): unknown {
    const parts: unknown[] = [];
    if (view.filter) {
      parts.push(view.filter);
    }
    const given = this.query()?.get('where');
    if (given) {
      try {
        parts.push(JSON.parse(given));
      } catch {
        // A broken address filters nothing.
      }
    }
    const q = this.query()?.get('q');
    if (q && this.searched().length) {
      parts.push({ or: this.searched().map((f) => ({ field: f, op: 'contains', value: q })) });
    }
    return parts.length === 0 ? null : parts.length === 1 ? parts[0] : { and: parts };
  }

  private searched(): string[] {
    return this.columns().filter((c) =>
      ['string', 'text', 'email'].includes(this.fields().get(c)?.type ?? ''),
    );
  }

  protected title(): string {
    return textIn(this.view()?.label, this.i18n.language());
  }

  protected label(name: string): string {
    return this.fields().get(name)?.label ?? name;
  }

  protected value(row: Row, name: string): string {
    return shown(row, this.fields().get(name), name, this.i18n.language(), (k) => this.i18n.t(k));
  }

  protected numeric(name: string): boolean {
    return NUMBER_TYPES.has(this.fields().get(name)?.type ?? '');
  }

  protected direction(name: string): 'ascending' | 'descending' | 'none' {
    const sort = this.sort()?.split(',')[0];
    return sort === name ? 'ascending' : sort === `-${name}` ? 'descending' : 'none';
  }

  protected sortBy(name: string): void {
    this.sort.set(this.direction(name) === 'ascending' ? `-${name}` : name);
    this.before.set([]);
    this.after.set(null);
    void this.load();
  }

  protected next(): void {
    this.before.update((b) => [...b, this.after()]);
    this.after.set(this.page()?.next ?? null);
    void this.load();
  }

  protected previous(): void {
    const earlier = [...this.before()];
    this.after.set(earlier.pop() ?? null);
    this.before.set(earlier);
    void this.load();
  }

  protected open_(row: Row): void {
    void this.router.navigate(['/form', this.view()!.table, row.sys_id]);
  }

  protected range(): string {
    const page = this.page();
    if (!page || !page.records.length) {
      return '';
    }
    const from = this.first() + 1;
    const to = this.first() + page.records.length;
    const total = page.total ?? to;
    return this.i18n.t(page.total_exact ? 'list.range' : 'list.range_about', { from, to, total });
  }

  protected create(): void {
    void this.router.navigate(['/form', this.view()!.table, 'new']);
  }
}
