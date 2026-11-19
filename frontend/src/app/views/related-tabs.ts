import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTabsModule } from '@angular/material/tabs';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api, describe } from '../core/api';
import { FieldInfo, TableInfo, shown } from '../core/format';
import { I18n } from '../core/i18n';
import { ListViews } from '../core/list-views';
import { Tables, path } from '../core/tables';
import { askToDelete } from './delete-dialog';
import { RecordDialog, RecordOf } from './record-dialog';
import { RelatedListsDialog } from './related-lists-dialog';

export interface Related {
  table: string;
  field: string;
  label: string;
  /** Declared by the form: always a tab, even when empty. */
  declared: boolean;
  /** A link to any record: its table field's condition too. */
  also?: Record<string, string>;
}

interface Opened {
  related: Related;
  info: TableInfo;
  columns: string[];
  rows: Record<string, unknown>[];
  /** Paging: the cursor this page was loaded with, the next page's, and those of the pages
   * before (null: the first page). */
  after: string | null;
  next: string | null;
  before: (string | null)[];
  page: number;
  /** A column to sort by (`-` first: descending), and the words searched for. */
  sort: string | null;
  search: string;
}

const MAX_ROWS = 20;
// Fields a search looks in: those read as text.
const SEARCHED = new Set(['text', 'translated']);
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
  imports: [NgTemplateOutlet, MatButtonModule, MatTabsModule, RouterLink],
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
  private readonly snack = inject(MatSnackBar);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  readonly lists = input.required<Related[]>();
  /** The record these lists belong to, by name: a new line shows it. */
  readonly name = input<string>('');

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

  /** The records of a list: those pointing here (and, for links to any record, of this table). */
  private filters(r: Related, id: string): Record<string, string> {
    return {
      [`filter[${r.field}]`]: id,
      ...Object.fromEntries(Object.entries(r.also ?? {}).map(([k, v]) => [`filter[${k}]`, v])),
    };
  }

  /** Counts every list; then opens the one chosen before (or `stay`, after a change in it). */
  private async countAll(lists: Related[], id: string, stay?: Related): Promise<void> {
    const counts = new Map<string, number | null>();
    await Promise.all(
      lists.map(async (r) => {
        try {
          const { data } = await this.api.get<{ total: number | null }>(path(r.table), {
            query: { ...this.filters(r, id), limit: 1, fields: 'sys_id', count: true },
          });
          counts.set(this.key(r), data.total);
        } catch {
          counts.set(this.key(r), null);
        }
      }),
    );
    this.counts.set(counts);
    if (stay) {
      return;
    }
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

  /** The latest load: an earlier one finishing later doesn't replace what it shows. */
  private loads = 0;

  private async open(
    related: Related,
    after: string | null = null,
    before: (string | null)[] = [],
    sort: string | null = null,
    search = '',
  ): Promise<void> {
    const load = ++this.loads;
    const [info, view] = await Promise.all([
      this.tables.describe(related.table),
      this.views.view(related.table).catch(() => null),
    ]);
    const columns = (view?.columns ?? [info.display ?? 'sys_id']).filter(
      (c) => c !== related.field && info.fields.find((f) => f.name === c)?.read,
    );
    const words = search.trim();
    const searched = columns.filter((c) => SEARCHED.has(this.field(info, c)?.kind ?? ''));
    const where =
      words && searched.length
        ? JSON.stringify({
            or: searched.map((c) => ({ field: c, op: 'contains', value: words })),
          })
        : null;
    const { data } = await this.api.get<{
      records: Record<string, unknown>[];
      next: string | null;
    }>(path(related.table), {
      query: {
        ...this.filters(related, this.id()),
        limit: MAX_ROWS,
        sort: sort ?? view?.sort?.join(',') ?? '-updated',
        count: false,
        after,
        where,
      },
    });
    if (load !== this.loads) {
      return;
    }
    this.opened.set({
      related,
      info,
      columns,
      rows: data.records,
      after,
      next: data.next ?? null,
      before,
      page: before.length,
      sort,
      search,
    });
  }

  /** The open list again, from its first page: sorted, searched, or after a change. */
  private async again(changes: { sort?: string | null; search?: string } = {}): Promise<void> {
    const list = this.opened();
    if (!list) {
      return;
    }
    await this.open(
      list.related,
      null,
      [],
      changes.sort !== undefined ? changes.sort : list.sort,
      changes.search ?? list.search,
    );
  }

  /** A header click: by this column, then the other way round, then as the view sorts. */
  protected async sortBy(column: string): Promise<void> {
    const now = this.opened()?.sort;
    const next = now === column ? `-${column}` : now === `-${column}` ? null : column;
    await this.again({ sort: next });
  }

  protected sorted(list: Opened, column: string): 'ascending' | 'descending' | null {
    return list.sort === column ? 'ascending' : list.sort === `-${column}` ? 'descending' : null;
  }

  private searching: ReturnType<typeof setTimeout> | undefined;

  protected search(words: string): void {
    clearTimeout(this.searching);
    this.searching = setTimeout(() => void this.again({ search: words }), 300);
  }

  /** A modified click (new tab, new window) goes to the page itself. */
  private modified(event?: MouseEvent | KeyboardEvent): boolean {
    return !!event && (event.ctrlKey || event.metaKey || event.shiftKey);
  }

  private pageOf(commands: string[], query: Record<string, string> = {}): void {
    const url = this.router.serializeUrl(
      this.router.createUrlTree(commands, { queryParams: query }),
    );
    window.open(url, '_blank', 'noopener');
  }

  /** The page for a new line: where a modified or middle click goes. */
  protected newLink(related: Related): string {
    return this.router.serializeUrl(
      this.router.createUrlTree(['/form', related.table, 'new'], {
        queryParams: this.prefill(related),
      }),
    );
  }

  /** A new line here, in a dialog: it already belongs to this record. A modified click leaves
   * the link to the browser (a new tab). */
  protected async add(related: Related, event: MouseEvent): Promise<void> {
    if (this.modified(event)) {
      return;
    }
    event.preventDefault();
    await this.edit(related, null);
  }

  private async edit(related: Related, id: string | null): Promise<void> {
    const fixed = this.prefill(related);
    const data: RecordOf = {
      table: related.table,
      id,
      fixed,
      fixedNames: { [related.field]: this.name() },
    };
    const ref = this.dialog.open<RecordDialog, RecordOf, boolean>(RecordDialog, {
      data,
      width: '720px',
      maxWidth: '100vw',
    });
    if (await firstValueFrom(ref.afterClosed())) {
      // Read again first: rows keep the version a next change or delete is made on.
      await this.countAll(this.lists(), this.id(), related);
      await this.again();
      const label = (await this.tables.describe(related.table)).label;
      this.snack.open(
        id ? this.i18n.t('form.saved') : this.i18n.t('form.created', { label }),
        undefined,
        { duration: 2500 },
      );
    }
  }

  protected async remove(list: Opened, row: Record<string, unknown>): Promise<void> {
    const table = list.related.table;
    const id = String(row['sys_id']);
    const display = list.info.display;
    const name = display ? this.value(list.info, row, display) : id;
    const confirm = await askToDelete(this.dialog, { table, id, item: list.info.label, name });
    if (confirm === null) {
      return;
    }
    try {
      await this.api.delete(path(table, id), String(row['_etag']), { query: { confirm } });
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
      return;
    }
    await this.countAll(this.lists(), this.id(), list.related);
    await this.again();
    const undo = this.snack.open(this.i18n.t('list.deleted', { name }), this.i18n.t('list.undo'), {
      duration: 6000,
    });
    undo.onAction().subscribe(async () => {
      await this.api.post(`${path(table, id)}/restore`);
      await this.countAll(this.lists(), this.id(), list.related);
      await this.again();
    });
  }

  protected may(row: Record<string, unknown>, operation: string): boolean {
    return !!(row['_access'] as Record<string, boolean> | undefined)?.[operation];
  }

  /** The next page of the open list, or the one before. */
  protected async turn(by: 1 | -1): Promise<void> {
    const list = this.opened();
    if (by === 1 && list?.next) {
      await this.open(list.related, list.next, [...list.before, list.after]);
    } else if (by === -1 && list?.before.length) {
      await this.open(list.related, list.before.at(-1) ?? null, list.before.slice(0, -1));
    }
  }

  /** "21–40 of 1139" for the open page. */
  protected range(list: Opened): string {
    const first = list.page * MAX_ROWS + 1;
    const last = list.page * MAX_ROWS + list.rows.length;
    const total = this.counts().get(this.key(list.related));
    return total === null || total === undefined
      ? `${first}–${last}`
      : this.i18n.t('related.range', { first, last, total });
  }

  protected field(info: TableInfo, name: string): FieldInfo | undefined {
    return info.fields.find((f) => f.name === name);
  }

  protected value(info: TableInfo, row: Record<string, unknown>, name: string): string {
    return shown(row, this.field(info, name), name, this.i18n.language(), (k) => this.i18n.t(k));
  }

  /** Where a reference in a row leads: the referenced record's form, if the person may read it
   * (then the row carries its name). */
  protected linkOf(info: TableInfo, row: Record<string, unknown>, name: string): string[] | null {
    const f = this.field(info, name);
    const table = f?.options.table;
    const id = row[name];
    const named = (row['_display'] as Record<string, unknown> | undefined)?.[name];
    if (f?.type !== 'reference' || !table || table === '-' || !id || named === undefined) {
      return null;
    }
    return ['/form', table, String(id)];
  }

  /** A new record that already points back here. */
  protected prefill(related: Related): Record<string, string> {
    return { [related.field]: this.id(), ...related.also };
  }

  /** A row opens in a dialog; with Ctrl or Cmd, its page. */
  protected openRow(related: Related, row: Record<string, unknown>, event?: MouseEvent): void {
    const id = String(row['sys_id']);
    if (this.modified(event)) {
      this.pageOf(['/form', related.table, id]);
      return;
    }
    void this.edit(related, id);
  }

  protected count(r: Related): string {
    const n = this.counts().get(this.key(r));
    return n === undefined ? '·' : n === null ? '?' : String(n);
  }
}
