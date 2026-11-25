import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, computed, effect, inject, signal, untracked, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatChipsModule } from '@angular/material/chips';
import { MatDialog } from '@angular/material/dialog';
import { MatMenuModule, MatMenuTrigger } from '@angular/material/menu';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom, map } from 'rxjs';

import { ActionDef, Actions } from '../core/actions';
import { Api, Problem, describe } from '../core/api';
import {
  Condition,
  NO_VALUE,
  fromParams,
  operatorKey,
  relative,
  toParams,
  toWhere,
} from '../core/filters';
import { FieldInfo, NUMBER_TYPES, SUMMED_TYPES, TableInfo, shown, textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { ListView, ListViews, Remembered, SavedView, conditionsOf } from '../core/list-views';
import { Preferences } from '../core/preferences';
import { Tables, path } from '../core/tables';
import { ProblemState } from '../pages/problem-state';
import { ActionRunner } from './action-runner';
import { ColumnsDialog, ColumnsRequest } from './columns-dialog';
import { ConfirmDialog } from './confirm-dialog';
import { askToDelete } from './delete-dialog';
import { FilterDialog, FilterRequest } from './filter-dialog';
import { SaveView, SaveViewDialog } from './save-view-dialog';
import { WidgetChoice, WidgetFromList, WidgetFromListDialog } from './widget-from-list-dialog';
import { Update, UpdateDialog, UpdateRequest } from './update-dialog';

type Row = Record<string, unknown> & { sys_id: string; _etag: string };

interface Page {
  records: Row[];
  next: string | null;
  total: number | null;
  total_exact: boolean;
}

interface AggregateAnswer {
  total: unknown;
  groups: { key: unknown; label: string; value: unknown }[];
}

interface CellMenu {
  row: Row;
  field: FieldInfo;
  empty: boolean;
}

/** Types whose values make poor filters from a cell (long, structured or secret). */
const NOT_FROM_CELLS = new Set(['text', 'markdown', 'json', 'secret', 'translated_text']);
const DEFAULT_SORT = ['-updated'];

/** Any table's list: columns, sort, filters and search in the address, saved views, cards on
 * small screens, right-click menus, and changes to many records at once. */
@Component({
  selector: 'app-list-page',
  imports: [
    MatButtonModule,
    MatCheckboxModule,
    MatChipsModule,
    MatMenuModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    ProblemState,
  ],
  templateUrl: './list-page.html',
  styleUrl: './list-page.scss',
})
export class ListPage {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly tables = inject(Tables);
  private readonly views = inject(ListViews);
  private readonly preferences = inject(Preferences);
  private readonly actionsApi = inject(Actions);
  private readonly runner = inject(ActionRunner);
  private readonly params = toSignal(this.route.paramMap);
  private readonly query = toSignal(this.route.queryParamMap);
  protected readonly cards = toSignal(
    inject(BreakpointObserver)
      .observe('(max-width: 719px)')
      .pipe(map((s) => s.matches)),
    { initialValue: window.matchMedia('(max-width: 719px)').matches },
  );
  private readonly cellTrigger = viewChild<MatMenuTrigger>('cellTrigger');
  private readonly headerTrigger = viewChild<MatMenuTrigger>('headerTrigger');

  protected readonly view = signal<ListView | null>(null);
  protected readonly table = signal<TableInfo | null>(null);
  protected readonly saved = signal<SavedView[]>([]);
  private readonly rememberedState = signal<Remembered | null>(null);
  /** Columns changed while a saved view is open: a draft until saved. */
  private readonly draftColumns = signal<string[] | null>(null);
  protected readonly page = signal<Page | null>(null);
  protected readonly busy = signal(false);
  protected readonly failed = signal<Problem | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly selected = signal(new Set<string>());
  protected readonly actionDefs = signal<ActionDef[]>([]);
  /** Actions for the chosen records: those at least one of them offers. */
  protected readonly bulk = computed(() => {
    const rows = (this.page()?.records ?? []).filter((r) => this.selected().has(r.sys_id));
    return this.actionDefs().filter(
      (a) => a.places.includes('list_bulk') && rows.some((r) => this.offers(r, a)),
    );
  });
  protected readonly names = signal(new Map<string, string>());
  protected readonly menuAt = signal({ x: 0, y: 0 });
  protected readonly cell = signal<CellMenu | null>(null);
  protected readonly header = signal<FieldInfo | null>(null);
  private readonly before = signal<(string | null)[]>([]);
  private readonly after = signal<string | null>(null);
  private opened: string | null = null;
  /** What the shown page was loaded for. */
  private loadedKey: string | null = null;
  private readonly stateKey = computed(() =>
    JSON.stringify([
      this.conditions(),
      this.sort(),
      this.search(),
      this.query()?.get('where') ?? null,
    ]),
  );

  /** Fields of referenced records shown as columns (`partner.city`), by their path. */
  private readonly walked = signal(new Map<string, FieldInfo>());
  protected readonly fields = computed(
    () =>
      new Map([...(this.table()?.fields ?? []).map((f) => [f.name, f] as const), ...this.walked()]),
  );
  /** The total of each number column over the whole filter, not just the page. */
  protected readonly totals = signal(new Map<string, string>());
  protected readonly conditions = computed(() => (this.query() ? fromParams(this.query()!) : []));
  protected readonly search = computed(() => this.query()?.get('q') ?? '');
  protected readonly active = computed(() => {
    const id = this.query()?.get('view');
    return this.saved().find((s) => s.sys_id === id) ?? null;
  });
  protected readonly sort = computed(() => {
    const given = this.query()?.get('sort');
    if (given) {
      return given.split(',');
    }
    return this.active()?.sort ?? this.rememberedState()?.sort ?? this.view()?.sort ?? DEFAULT_SORT;
  });
  private readonly chosenColumns = computed(
    () =>
      this.draftColumns() ??
      this.active()?.columns ??
      this.rememberedState()?.columns ??
      this.view()?.columns ??
      [],
  );
  protected readonly columns = computed(() =>
    // Columns a module removed, or the person may not read, simply don't show.
    this.chosenColumns().filter((c) => this.fields().get(c)?.read),
  );
  protected readonly changed = computed(() => {
    const view = this.active();
    if (!view) {
      return false;
    }
    const same = (a: unknown, b: unknown) =>
      JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
    return (
      !same(this.columns(), view.columns ?? this.view()?.columns) ||
      !same(this.sort(), view.sort ?? this.view()?.sort ?? DEFAULT_SORT) ||
      !same(toWhere(this.conditions()), view.filter) ||
      (this.search() || null) !== (view.search || null)
    );
  });
  protected readonly selectable = computed(() => !!this.table()?.operations['write']);
  protected readonly first = computed(
    () => this.before().length * this.preferences.values().page_size,
  );

  constructor() {
    // A column from a referenced record: its field described, then the page loaded with it.
    effect(() => {
      const paths = this.chosenColumns().filter((c) => c.includes('.'));
      if (this.table() && paths.some((p) => !untracked(() => this.walked()).has(p))) {
        void untracked(() => this.describeWalked(paths));
      }
    });
    effect(() => {
      const name = this.params()?.get('view');
      if (name && name !== this.opened) {
        this.opened = name;
        void untracked(() => this.open(name));
      }
    });
    // Filters, sort or search changed: from the first page again (only if they really did, so
    // a list opening doesn't load twice and lose what was chosen).
    effect(() => {
      const key = this.stateKey();
      if (untracked(() => this.view()) && key !== this.loadedKey) {
        untracked(() => {
          this.before.set([]);
          this.after.set(null);
          void this.load();
        });
      }
    });
  }

  private async open(name: string): Promise<void> {
    this.failed.set(null);
    this.view.set(null);
    this.page.set(null);
    this.draftColumns.set(null);
    this.selected.set(new Set());
    try {
      const view = await this.views.view(name);
      if (!view) {
        throw new Problem(404, 'not_found', 'Not found');
      }
      const [table, saved, remembered, actions] = await Promise.all([
        this.tables.describe(view.table),
        this.views.saved(view.name),
        this.views.remembered(view.name),
        this.actionsApi.of(view.table),
      ]);
      this.actionDefs.set(actions);
      this.table.set(table);
      void this.tables
        .describe('sys.widget')
        .then((w) => this.mayMakeWidgets.set(!!w.operations['create']))
        .catch(() => this.mayMakeWidgets.set(false));
      this.saved.set(saved);
      this.rememberedState.set(remembered);
      const params = this.query();
      const plain =
        params &&
        !params.keys.some((k) => k.startsWith('filter[') || ['sort', 'q', 'view'].includes(k));
      const preferred = saved.find((s) => s.is_default);
      this.view.set(view);
      if (plain && preferred) {
        await this.apply(preferred, true);
      } else {
        await this.load();
      }
    } catch (error) {
      this.failed.set(
        error instanceof Problem ? error : new Problem(500, 'error', describe(error)),
      );
    }
  }

  private async load(): Promise<void> {
    const view = this.view();
    if (!view) {
      return;
    }
    const where = this.whereNow();
    this.loadedKey = this.stateKey();
    this.busy.set(true);
    this.error.set(null);
    try {
      const { data } = await this.api.get<Page>(path(view.table), {
        query: {
          limit: Math.min(this.preferences.values().page_size, 500),
          sort: this.sort().join(','),
          after: this.after(),
          where: where ? JSON.stringify(where) : null,
          fields: this.fieldsParam(),
        },
      });
      this.page.set(data);
      this.selected.set(new Set());
      if (!this.before().length) {
        void this.loadTotals(where);
      }
      await this.nameReferences();
    } catch (error) {
      this.error.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  /** The fields behind columns like `partner.city`, labelled "Partner › City". */
  private async describeWalked(paths: string[]): Promise<void> {
    const found = new Map(this.walked());
    let added = false;
    for (const p of paths) {
      const [ref, name] = p.split('.');
      const via = this.table()?.fields.find((f) => f.name === ref);
      if (found.has(p) || !via?.options.table || !name) {
        continue;
      }
      try {
        const target = await this.tables.describe(via.options.table);
        const field = target.fields.find((f) => f.name === name);
        if (field) {
          found.set(p, { ...field, name: p, label: `${via.label} › ${field.label}` });
          added = true;
        }
      } catch {
        // A table they may not read: the column doesn't show.
      }
    }
    if (added) {
      this.walked.set(found);
      await this.load();
    }
  }

  /** The filter the list shows: the view's, the chips, a fixed one and the search. */
  private whereNow(): unknown {
    const view = this.view();
    const parts: unknown[] = [];
    if (view?.filter) {
      parts.push(view.filter);
    }
    const own = toWhere(this.conditions());
    if (own) {
      parts.push(own);
    }
    // A navigator item's filter that conditions can't express stays, fixed.
    const fixed = this.query()?.get('where');
    if (fixed) {
      try {
        parts.push(JSON.parse(fixed));
      } catch {
        // A broken address filters nothing.
      }
    }
    const q = this.search();
    const searched = (view?.search ?? []).filter((f) => this.fields().get(f)?.read);
    if (q && searched.length) {
      parts.push({ or: searched.map((f) => ({ field: f, op: 'contains', value: q })) });
    }
    return parts.length === 0 ? null : parts.length === 1 ? parts[0] : { and: parts };
  }

  /** The list's own fields and the referenced ones, when it shows any of those. */
  private fieldsParam(): string | null {
    const walked = this.columns().filter((c) => c.includes('.'));
    if (!walked.length) {
      return null;
    }
    const own = (this.table()?.fields ?? []).filter((f) => f.read).map((f) => f.name);
    return [...own, ...walked].join(',');
  }

  /** Each number column's total over the whole filter; amounts per currency. */
  private async loadTotals(where: unknown): Promise<void> {
    const view = this.view();
    const summed = this.columns().filter(
      (c) => !c.includes('.') && SUMMED_TYPES.has(this.fields().get(c)?.type ?? ''),
    );
    if (!view || !summed.length) {
      this.totals.set(new Map());
      return;
    }
    const found = new Map<string, string>();
    await Promise.all(
      summed.map(async (name) => {
        const field = this.fields().get(name)!;
        const holder = field.options['currency_field'];
        const byCurrency = typeof holder === 'string' && this.fields().get(holder)?.read;
        try {
          const { data } = await this.api.get<AggregateAnswer>(`${path(view.table)}/aggregate`, {
            query: {
              measure: 'sum',
              field: name,
              group: byCurrency ? holder : null,
              limit: 10,
              where: where ? JSON.stringify(where) : null,
            },
            quiet: true,
          });
          const parts = byCurrency
            ? data.groups.map((g) => this.amount(field, g.value, String(g.key ?? '')))
            : [this.amount(field, data.total, '')];
          found.set(name, parts.filter((p) => p).join(' · ') || '—');
        } catch {
          // No total for a column they can't add up.
        }
      }),
    );
    this.totals.set(found);
  }

  private amount(field: FieldInfo, value: unknown, currency: string): string {
    if (value === null || value === undefined) {
      return '';
    }
    const options: FieldInfo['options'] = { ...field.options, currency_field: null };
    if (currency) {
      options['currency'] = currency;
    }
    const fixed: FieldInfo = { ...field, options };
    return shown({ [field.name]: value }, fixed, field.name, this.i18n.language(), (k) =>
      this.i18n.t(k),
    );
  }

  /** The address of the list as a file, with its filter, sort and columns. */
  protected exportLink(format: 'csv' | 'xlsx' | 'json'): string {
    const view = this.view();
    if (!view) {
      return '';
    }
    const query = new URLSearchParams({
      format,
      fields: this.columns().join(','),
      sort: this.sort().join(','),
    });
    const where = this.whereNow();
    if (where) {
      query.set('where', JSON.stringify(where));
    }
    return `${path(view.table)}/export?${query.toString()}`;
  }

  /** Names for the records the chips point to, from the page or the API. */
  private async nameReferences(): Promise<void> {
    const wanted = new Map<string, string[]>();
    for (const c of this.conditions()) {
      const field = this.fields().get(c.field);
      const table = field?.options.table;
      if (field?.kind !== 'reference' || !table || !c.value) {
        continue;
      }
      for (const id of [c.value].flat().filter((v) => !v.startsWith('@') && !this.names().has(v))) {
        wanted.set(table, [...(wanted.get(table) ?? []), id]);
      }
    }
    for (const [table, ids] of wanted) {
      try {
        const found = await this.tables.names(table, ids);
        this.names.update((all) => new Map([...all, ...found]));
      } catch {
        // Unnamed, they show as IDs.
      }
    }
  }

  // ---- the address ----

  private go(changes: Record<string, string | string[] | null>, replace = false): Promise<boolean> {
    return this.router.navigate([], {
      queryParams: changes,
      queryParamsHandling: 'merge',
      replaceUrl: replace,
    });
  }

  protected setConditions(conditions: Condition[]): void {
    void this.go(toParams(conditions, this.query() ?? null));
  }

  protected addCondition(condition: Condition): void {
    this.setConditions([...this.conditions(), condition]);
  }

  protected removeCondition(index: number): void {
    this.setConditions(this.conditions().filter((_, i) => i !== index));
  }

  protected clearSearch(): void {
    void this.go({ q: null });
  }

  protected sortBy(name: string, direction?: 'asc' | 'desc'): void {
    const now = this.direction(name);
    const ascending = direction ? direction === 'asc' : now !== 'ascending';
    const sort = [ascending ? name : `-${name}`];
    void this.go({ sort: sort.join(',') });
    if (!this.active()) {
      void this.rememberValues({ sort });
    }
  }

  private async rememberValues(values: Partial<Remembered>): Promise<void> {
    try {
      this.rememberedState.set(
        await this.views.remember(this.view()!.name, this.rememberedState(), values),
      );
    } catch {
      // Remembering is a convenience; the list works without it.
    }
  }

  // ---- views ----

  protected async apply(saved: SavedView | null, replace = false): Promise<void> {
    this.draftColumns.set(null);
    const conditions = saved ? conditionsOf(saved.filter) : [];
    await this.go(
      {
        ...toParams(conditions, this.query() ?? null),
        sort: saved?.sort?.join(',') ?? null,
        q: saved?.search ?? null,
        view: saved?.sys_id ?? 'default',
      },
      replace,
    );
  }

  private current(): Partial<SavedView> {
    return {
      columns: this.columns(),
      sort: this.sort(),
      filter: toWhere(this.conditions()),
      search: this.search() || null,
    };
  }

  protected async saveChanges(): Promise<void> {
    const view = this.active();
    if (!view) {
      return;
    }
    await this.run(async () => {
      const changed = await this.views.change(view, this.current());
      this.saved.update((all) => all.map((s) => (s.sys_id === changed.sys_id ? changed : s)));
      this.draftColumns.set(null);
      this.snack.open(this.i18n.t('view.saved', { name: changed.name }), undefined, {
        duration: 3000,
      });
    });
  }

  /** Widget editors make a widget from the list, with its filter. */
  protected readonly mayMakeWidgets = signal(false);

  protected makeWidget(): void {
    const view = this.view();
    if (!view) {
      return;
    }
    this.dialog
      .open<WidgetFromListDialog, WidgetFromList, WidgetChoice>(WidgetFromListDialog, {
        data: {
          title: this.table()?.label_plural ?? view.name,
          fields: this.table()?.fields ?? [],
        },
      })
      .afterClosed()
      .subscribe(async (choice) => {
        if (!choice) {
          return;
        }
        try {
          const { data } = await this.api.post<{ sys_id: string }>('/api/v1/sys/widget', {
            title: choice.title,
            kind: choice.kind,
            list: view.name,
            filter: this.whereNow(),
            group: choice.group,
            chart: choice.kind === 'chart' ? 'bar' : null,
          });
          void this.router.navigate(['/form/sys.widget', data.sys_id]);
        } catch (error) {
          this.snack.open(describe(error), this.i18n.t('common.close'));
        }
      });
  }

  protected saveAs(): void {
    const name = this.active() ? this.i18n.t('view.copy', { name: this.active()!.name }) : '';
    this.dialog
      .open(SaveViewDialog, { data: { name } })
      .afterClosed()
      .subscribe((answer?: SaveView) => {
        if (!answer) {
          return;
        }
        void this.run(async () => {
          const made = await this.views.save(this.view()!.name, {
            ...this.current(),
            name: answer.name,
            is_default: answer.isDefault,
          });
          this.saved.set(await this.views.saved(this.view()!.name));
          this.draftColumns.set(null);
          await this.go({ view: made.sys_id });
          this.snack.open(this.i18n.t('view.saved', { name: made.name }), undefined, {
            duration: 3000,
          });
        });
      });
  }

  protected toggleDefault(): Promise<void> {
    const view = this.active()!;
    return this.run(async () => {
      await this.views.change(view, { is_default: !view.is_default });
      this.saved.set(await this.views.saved(this.view()!.name));
    });
  }

  protected async deleteView(): Promise<void> {
    const view = this.active()!;
    const sure = await this.confirm(
      this.i18n.t('view.delete_title', { name: view.name }),
      this.i18n.t('view.delete_text'),
      this.i18n.t('list.delete_view'),
    );
    if (sure) {
      await this.run(async () => {
        await this.views.remove(view);
        this.saved.update((all) => all.filter((s) => s.sys_id !== view.sys_id));
        await this.apply(null);
      });
    }
  }

  protected async reset(): Promise<void> {
    await this.run(async () => {
      const found = this.rememberedState();
      if (found) {
        await this.views.forget(found);
        this.rememberedState.set(null);
      }
      await this.apply(null);
    });
  }

  // ---- dialogs ----

  protected addFilter(only?: string): void {
    this.dialog
      .open<FilterDialog, FilterRequest, Condition>(FilterDialog, {
        data: { fields: this.table()?.fields ?? [], only },
      })
      .afterClosed()
      .subscribe((condition) => condition && this.addCondition(condition));
  }

  protected chooseColumns(): void {
    const available = this.availableFields();
    this.dialog
      .open<ColumnsDialog, ColumnsRequest, string[]>(ColumnsDialog, {
        data: {
          shown: this.columns(),
          available: [...available, ...this.walked().values()],
          references: available.filter((f) => f.kind === 'reference' && !!f.options.table),
        },
      })
      .afterClosed()
      .subscribe((columns) => columns && this.setColumns(columns));
  }

  private availableFields(): FieldInfo[] {
    const allowed = this.view()?.available;
    return (this.table()?.fields ?? []).filter(
      (f) =>
        f.read &&
        f.name !== 'sys_id' &&
        (!allowed?.length || allowed.includes(f.name) || this.view()!.columns.includes(f.name)),
    );
  }

  protected setColumns(columns: string[]): void {
    this.totals.set(new Map());
    if (this.active()) {
      this.draftColumns.set(columns);
    } else {
      this.draftColumns.set(null);
      this.rememberedState.update((r) => (r ? { ...r, columns } : r));
      void this.rememberValues({ columns });
    }
    // A referenced record's field already described needs the page again; totals follow.
    const shown = new Set(Object.keys(this.page()?.records[0] ?? {}));
    if (columns.some((c) => c.includes('.') && this.walked().has(c) && !shown.has(c))) {
      void this.load();
    } else {
      void this.loadTotals(this.whereNow());
    }
  }

  protected hide(name: string): void {
    this.setColumns(this.columns().filter((c) => c !== name));
  }

  private async confirm(title: string, text: string, action: string): Promise<boolean> {
    const ref = this.dialog.open(ConfirmDialog, { data: { title, text, action } });
    return !!(await firstValueFrom(ref.afterClosed()));
  }

  private async run(work: () => Promise<void>): Promise<void> {
    try {
      await work();
    } catch (error) {
      this.snack.open(
        describe(error, (f) => this.fields().get(f)?.label ?? f),
        this.i18n.t('common.close'),
      );
    }
  }

  // ---- records ----

  protected openRecord(row: Row): void {
    void this.router.navigate(['/form', this.view()!.table, row.sys_id]);
  }

  protected create(): void {
    void this.router.navigate(['/form', this.view()!.table, 'new']);
  }

  protected title(): string {
    return textIn(this.view()?.label, this.i18n.language());
  }

  private item(): string {
    return (this.table()?.label ?? '').toLowerCase();
  }

  protected rowName(row: Row): string {
    const display = this.table()?.display;
    return display ? this.value(row, display) : row.sys_id;
  }

  protected can(row: Row, operation: string): boolean {
    return !!(row['_access'] as Record<string, boolean> | undefined)?.[operation];
  }

  protected async remove(row: Row): Promise<void> {
    const name = this.rowName(row);
    const table = this.view()!.table;
    const confirm = await askToDelete(this.dialog, {
      table,
      id: row.sys_id,
      item: this.table()?.label ?? '',
      name,
    });
    if (confirm === null) {
      return;
    }
    await this.run(async () => {
      await this.api.delete(path(table, row.sys_id), row._etag, { query: { confirm } });
      await this.load();
      const undo = this.snack.open(
        this.i18n.t('list.deleted', { name }),
        this.i18n.t('list.undo'),
        {
          duration: 6000,
        },
      );
      undo.onAction().subscribe(() =>
        this.run(async () => {
          await this.api.post(`${path(table, row.sys_id)}/restore`);
          await this.load();
        }),
      );
    });
  }

  // ---- selection ----

  protected toggle(row: Row, on: boolean): void {
    this.selected.update((all) => {
      const copy = new Set(all);
      if (on) {
        copy.add(row.sys_id);
      } else {
        copy.delete(row.sys_id);
      }
      return copy;
    });
  }

  protected allSelected(): boolean {
    const rows = this.page()?.records ?? [];
    return rows.length > 0 && rows.every((r) => this.selected().has(r.sys_id));
  }

  protected selectAll(on: boolean): void {
    this.selected.set(on ? new Set((this.page()?.records ?? []).map((r) => r.sys_id)) : new Set());
  }

  /** One field on every chosen record, each checked and recorded on its own. */
  protected updateSelected(): void {
    const rows = (this.page()?.records ?? []).filter((r) => this.selected().has(r.sys_id));
    this.dialog
      .open<UpdateDialog, UpdateRequest, Update>(UpdateDialog, {
        data: { count: rows.length, fields: this.table()?.fields ?? [] },
      })
      .afterClosed()
      .subscribe(async (update) => {
        if (!update) {
          return;
        }
        const refused: string[] = [];
        let done = 0;
        for (const row of rows) {
          try {
            await this.api.patch(
              path(this.view()!.table, row.sys_id),
              { [update.field]: update.value },
              row._etag,
            );
            done += 1;
          } catch (error) {
            refused.push(
              `${this.rowName(row)}: ${describe(error, (f) => this.fields().get(f)?.label ?? f)}`,
            );
          }
        }
        await this.load();
        if (refused.length) {
          this.snack.open(
            `${this.i18n.t('update.partly', { n: done, m: refused.length })} · ${refused.slice(0, 3).join(' · ')}`,
            this.i18n.t('common.close'),
          );
        } else {
          this.snack.open(this.i18n.t('update.done', { n: done }), undefined, { duration: 3000 });
        }
      });
  }

  // ---- actions ----

  private offers(row: Row, action: ActionDef): boolean {
    return ((row['_actions'] as string[] | undefined) ?? []).includes(action.name);
  }

  protected rowActions(row: Row): ActionDef[] {
    return this.actionDefs().filter((a) => a.places.includes('list_row') && this.offers(row, a));
  }

  protected actionLabel(action: ActionDef): string {
    return this.runner.label(action);
  }

  protected async actOn(row: Row, action: ActionDef): Promise<void> {
    if (await this.runner.one(action, row.sys_id, row._etag)) {
      await this.load();
    }
  }

  /** On each chosen record that offers it; the others are left out, not refused. */
  protected async actOnSelected(action: ActionDef): Promise<void> {
    const ids = (this.page()?.records ?? [])
      .filter((r) => this.selected().has(r.sys_id) && this.offers(r, action))
      .map((r) => r.sys_id);
    if (await this.runner.many(action, ids)) {
      await this.load();
    }
  }

  // ---- showing values ----

  protected label(name: string): string {
    return this.fields().get(name)?.label ?? name;
  }

  protected value(row: Row, name: string): string {
    return shown(row, this.fields().get(name), name, this.i18n.language(), (k) => this.i18n.t(k));
  }

  /** How a choice stands out: its declared tone. */
  protected tone(row: Row, name: string): string | null {
    const field = this.fields().get(name);
    if (field?.kind !== 'choice') {
      return null;
    }
    return field.options.values?.find((v) => v.value === row[name])?.tone ?? null;
  }

  protected numeric(name: string): boolean {
    return NUMBER_TYPES.has(this.fields().get(name)?.type ?? '');
  }

  protected direction(name: string): 'ascending' | 'descending' | 'none' {
    const first = this.sort()[0];
    return first === name ? 'ascending' : first === `-${name}` ? 'descending' : 'none';
  }

  protected sortable(name: string): boolean {
    return this.fields().get(name)?.kind != null;
  }

  /** A condition in words, for its chip. */
  protected chip(c: Condition): string {
    const field = this.fields().get(c.field);
    const label = field?.label ?? c.field;
    const t = (k: Parameters<I18n['t']>[0], p?: Record<string, string | number>) =>
      this.i18n.t(k, p);
    if (c.value === '@me') {
      return `${label} ${t('op.is_me')}`;
    }
    if (Array.isArray(c.value) && c.value[0] === '@my_groups') {
      return `${label} ${t('op.my_groups')}`;
    }
    const words = t(operatorKey(c.op, field?.kind ?? null));
    if (NO_VALUE.has(c.op)) {
      return `${label} ${words}`;
    }
    const one = (v: string): string => {
      if (field?.kind === 'choice') {
        return field.options.values?.find((o) => o.value === v)?.label ?? v;
      }
      if (field?.kind === 'boolean') {
        return t(v === 'true' ? 'common.yes' : 'common.no');
      }
      if (field?.kind === 'reference') {
        return this.names().get(v) ?? v;
      }
      if (field?.kind === 'date' || field?.kind === 'datetime') {
        return (
          relative(v, t) ??
          new Date(`${v.slice(0, 10)}T00:00:00`).toLocaleDateString(this.i18n.language())
        );
      }
      if (field?.type === 'percent') {
        return `${Number(v) * 100} %`;
      }
      return v;
    };
    const values = [c.value ?? ''].flat().map(one);
    const joined = c.op === 'between' ? values.join(` ${t('list.and')} `) : values.join(', ');
    return `${label} ${words} ${joined}`;
  }

  // ---- right-click menus ----

  protected cellMenu(event: MouseEvent, row: Row, name: string): void {
    const field = this.fields().get(name);
    if (event.ctrlKey || event.metaKey || event.shiftKey || !field) {
      return; // the browser's own menu
    }
    event.preventDefault();
    const raw = row[name];
    this.cell.set({ row, field, empty: raw === null || raw === undefined || raw === '' });
    this.menuAt.set({ x: event.clientX, y: event.clientY });
    this.cellTrigger()?.openMenu();
  }

  protected headerMenu(event: MouseEvent, name: string): void {
    const field = this.fields().get(name);
    if (event.ctrlKey || event.metaKey || event.shiftKey || !field) {
      return;
    }
    event.preventDefault();
    this.header.set(field);
    this.menuAt.set({ x: event.clientX, y: event.clientY });
    this.headerTrigger()?.openMenu();
  }

  /** Whether the menu may filter by this cell (and how). */
  protected filterable(menu: CellMenu): boolean {
    const f = menu.field;
    return !!f.kind && f.read && !NOT_FROM_CELLS.has(f.type) && menu.field.name in menu.row;
  }

  protected ordered(menu: CellMenu): 'date' | 'number' | null {
    const kind = menu.field.kind;
    return kind === 'date' || kind === 'datetime' ? 'date' : kind === 'number' ? 'number' : null;
  }

  protected filterBy(menu: CellMenu, op: string): void {
    const raw = menu.row[menu.field.name];
    const value = menu.field.kind === 'datetime' && typeof raw === 'string' ? raw : String(raw);
    this.addCondition(
      NO_VALUE.has(op) ? { field: menu.field.name, op } : { field: menu.field.name, op, value },
    );
  }

  protected referenced(menu: CellMenu): { table: string; id: string; name: string } | null {
    const table = menu.field.options.table;
    if (menu.field.kind !== 'reference' || !table || table === '-' || menu.empty) {
      return null;
    }
    return {
      table,
      id: String(menu.row[menu.field.name]),
      name: this.value(menu.row, menu.field.name),
    };
  }

  protected openReferenced(menu: CellMenu): void {
    const found = this.referenced(menu);
    if (found) {
      void this.router.navigate(['/form', found.table, found.id]);
    }
  }

  protected async copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.snack.open(this.i18n.t('common.copied'), undefined, { duration: 1500 });
    } catch {
      this.snack.open(this.i18n.t('menu.copy_failed'), undefined, { duration: 3000 });
    }
  }

  protected link(row: Row): string {
    return `${window.location.origin}/form/${this.view()!.table}/${row.sys_id}`;
  }

  // ---- paging ----

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

  protected none(): string {
    return this.i18n.t('list.none', {
      items: (this.table()?.label_plural ?? this.title()).toLowerCase(),
    });
  }

  /** The other columns of a card, after its title and main line. */
  protected cardMeta(): string[] {
    return this.columns().slice(2);
  }
}
