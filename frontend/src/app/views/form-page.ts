import {
  Component,
  HostListener,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDialog } from '@angular/material/dialog';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatRadioModule } from '@angular/material/radio';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api, Problem, describe } from '../core/api';
import { FieldInfo, TableInfo, shown, textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { Tables, path } from '../core/tables';
import { FieldHost } from '../field-types/field-host';
import { unitOf } from '../field-types/registry';
import { ProblemState } from '../pages/problem-state';
import { ConfirmDialog } from './confirm-dialog';
import { FieldDisplay } from './field-display';
import { Related, RelatedTabs } from './related-tabs';

type Values = Record<string, unknown>;

interface FormSection {
  title: unknown;
  fields: string[];
  columns: number;
}

interface FormView {
  sections: FormSection[];
  related: { table: string; field: string }[] | null;
  related_auto: boolean;
}

interface Conflict {
  current: Values;
  /** Fields both changed: whose value to keep. */
  rows: { field: FieldInfo; theirs: string; mine: string; keep: 'theirs' | 'mine' }[];
}

const SYSTEM = new Set(['sys_id', 'created', 'created_by', 'updated', 'updated_by', 'source']);

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** Any record of any table, shown and edited in the sections of its form view. */
@Component({
  selector: 'app-form-page',
  imports: [
    FieldDisplay,
    MatButtonModule,
    MatCardModule,
    MatProgressBarModule,
    MatRadioModule,
    MatTooltipModule,
    FieldHost,
    ProblemState,
    RelatedTabs,
  ],
  templateUrl: './form-page.html',
  styleUrl: './form-page.scss',
})
export class FormPage {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly params = toSignal(this.route.paramMap);

  protected readonly table = signal<string>('');
  protected readonly id = signal<string>('new');
  protected readonly info = signal<TableInfo | null>(null);
  protected readonly form = signal<FormView | null>(null);
  protected readonly original = signal<Values>({});
  protected readonly values = signal<Values>({});
  protected readonly errors = signal<Record<string, string>>({});
  protected readonly busy = signal(false);
  protected readonly failed = signal<Problem | null>(null);
  protected readonly conflict = signal<Conflict | null>(null);
  protected readonly draft = signal<Values | null>(null);

  protected readonly creating = computed(() => this.id() === 'new');
  protected readonly fields = computed(
    () => new Map((this.info()?.fields ?? []).map((f) => [f.name, f])),
  );
  protected readonly access = computed(
    () => (this.original()['_access'] as Record<string, boolean> | undefined) ?? {},
  );
  protected readonly deleted = computed(() => !!this.original()['deleted']);
  protected readonly writable = computed(() =>
    this.creating()
      ? !!this.info()?.operations['create']
      : !!this.access()['write'] && !this.deleted(),
  );
  protected readonly changed = computed(() =>
    Object.keys(this.values()).filter(
      (k) => !SYSTEM.has(k) && !k.startsWith('_') && !same(this.values()[k], this.original()[k]),
    ),
  );
  protected readonly sections = computed(() =>
    (this.form()?.sections ?? [])
      .map((s) => ({
        title: textIn(s.title, this.i18n.language()),
        columns: Math.max(1, Math.min(4, s.columns)),
        fields: s.fields
          .map((n) => this.fields().get(n))
          .filter((f): f is FieldInfo => !!f && this.visible(f)),
      }))
      .filter((s) => s.fields.length),
  );
  protected readonly related = computed<Related[]>(() => {
    const info = this.info();
    const form = this.form();
    if (!info || !form || this.creating()) {
      return [];
    }
    const found: Related[] = [];
    const label = (table: string, field: string) =>
      info.referenced_by.find((r) => r.table === table && r.field === field)?.label ?? table;
    for (const r of form.related ?? []) {
      if (info.referenced_by.some((x) => x.table === r.table && x.field === r.field)) {
        found.push({ ...r, label: label(r.table, r.field), declared: true });
      }
    }
    if (form.related_auto) {
      for (const r of info.referenced_by) {
        if (!found.some((x) => x.table === r.table && x.field === r.field)) {
          found.push({ table: r.table, field: r.field, label: r.label, declared: false });
        }
      }
    }
    return found;
  });

  constructor() {
    effect(() => {
      const params = this.params();
      const table = params?.get('table');
      const id = params?.get('id');
      if (table && id) {
        void untracked(() => this.open(table, id));
      }
    });
    // Unsaved changes stay in this browser tab, offered back if the form is left by accident.
    effect(() => {
      const changed = this.changed();
      const values = this.values();
      untracked(() => this.keepDraft(changed, values));
    });
  }

  private draftKey(): string {
    return `p42.draft.${this.table()}.${this.id()}`;
  }

  private keepDraft(changed: string[], values: Values): void {
    try {
      const kept = changed.filter((k) => this.fields().get(k)?.type !== 'secret');
      if (kept.length && this.info()) {
        sessionStorage.setItem(
          this.draftKey(),
          JSON.stringify(Object.fromEntries(kept.map((k) => [k, values[k]]))),
        );
      } else if (this.info()) {
        sessionStorage.removeItem(this.draftKey());
      }
    } catch {
      // Without storage, drafts simply aren't kept.
    }
  }

  private readDraft(): Values | null {
    try {
      const found = sessionStorage.getItem(this.draftKey());
      return found ? (JSON.parse(found) as Values) : null;
    } catch {
      return null;
    }
  }

  private async open(table: string, id: string): Promise<void> {
    this.failed.set(null);
    this.conflict.set(null);
    this.errors.set({});
    this.info.set(null);
    this.table.set(table);
    this.id.set(id);
    try {
      const info = await this.tables.describe(table);
      const form = await this.formOf(info);
      let record: Values = {};
      if (id !== 'new') {
        record = (await this.api.get<Values>(path(table, id))).data;
      } else {
        record = this.defaults(info);
      }
      this.form.set(form);
      this.original.set(id === 'new' ? {} : record);
      this.values.set({ ...record });
      this.draft.set(this.readDraft());
      this.info.set(info);
    } catch (error) {
      this.failed.set(
        error instanceof Problem ? error : new Problem(500, 'error', describe(error)),
      );
    }
  }

  /** A new record: declared defaults, and values handed over in the address (a related list's
   * "New" sets the record it belongs to). */
  private defaults(info: TableInfo): Values {
    const values: Values = {};
    const given = this.route.snapshot.queryParamMap;
    for (const f of info.fields) {
      if (f.system || !f.create) {
        continue;
      }
      const passed = given.get(f.name);
      if (passed !== null) {
        values[f.name] = passed;
      } else if (f.default !== null && f.default !== undefined) {
        values[f.name] = f.default;
      }
    }
    return values;
  }

  private async formOf(info: TableInfo): Promise<FormView> {
    try {
      const { data } = await this.api.get<{ records: FormView[] }>('/api/v1/sys/form_view', {
        query: { 'filter[name]': info.table, count: false },
      });
      if (data.records[0]) {
        return data.records[0];
      }
    } catch {
      // Made from the fields below.
    }
    return {
      sections: [
        {
          title: info.label,
          fields: info.fields.filter((f) => !f.system).map((f) => f.name),
          columns: 2,
        },
      ],
      related: null,
      related_auto: true,
    };
  }

  protected visible(field: FieldInfo): boolean {
    if (this.creating()) {
      return !field.system && field.create;
    }
    return field.read;
  }

  protected editable(field: FieldInfo): boolean {
    if (field.system || !this.writable()) {
      return false;
    }
    return this.creating() ? field.create : field.write;
  }

  protected wide(field: FieldInfo): boolean {
    return !!unitOf(field.type).wide;
  }

  protected displayed(field: FieldInfo): string | null {
    const display = (this.original()['_display'] as Values | undefined)?.[field.name];
    return display === undefined || display === null ? null : String(display);
  }

  protected set(name: string, value: unknown): void {
    this.values.update((all) => ({ ...all, [name]: value }));
    if (this.errors()[name]) {
      this.errors.update((all) => {
        const copy = { ...all };
        delete copy[name];
        return copy;
      });
    }
  }

  protected title(): string {
    const info = this.info();
    if (!info) {
      return '';
    }
    if (this.creating()) {
      return this.i18n.t('form.new', { label: info.label });
    }
    return `${info.label} ${this.name()}`.trim();
  }

  protected name(): string {
    const display = this.info()?.display;
    if (!display) {
      return '';
    }
    return shown(this.original(), this.fields().get(display), display, this.i18n.language(), (k) =>
      this.i18n.t(k),
    );
  }

  protected meta(): string {
    const record = this.original();
    if (this.creating() || !record['created']) {
      return '';
    }
    const display = (record['_display'] as Values | undefined) ?? {};
    const when = (v: unknown) =>
      new Date(String(v)).toLocaleString(this.i18n.language(), {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
    return [
      this.i18n.t('form.created_by', {
        when: when(record['created']),
        who: String(display['created_by'] ?? '—'),
      }),
      this.i18n.t('form.updated_by', {
        when: when(record['updated']),
        who: String(display['updated_by'] ?? '—'),
      }),
    ].join(' · ');
  }

  // ---- saving ----

  private required(): boolean {
    const missing: Record<string, string> = {};
    for (const section of this.sections()) {
      for (const f of section.fields) {
        const v = this.values()[f.name];
        if (
          this.editable(f) &&
          f.required &&
          f.type !== 'boolean' &&
          (v === null || v === undefined || v === '')
        ) {
          missing[f.name] = this.i18n.t('form.required', { label: f.label });
        }
      }
    }
    this.errors.set(missing);
    return Object.keys(missing).length === 0;
  }

  protected async save(): Promise<void> {
    if (!this.changed().length || this.busy() || !this.required()) {
      return;
    }
    const body = Object.fromEntries(this.changed().map((k) => [k, this.values()[k]]));
    await this.send(body, String(this.original()['_etag'] ?? ''));
  }

  private async send(body: Values, etag: string): Promise<void> {
    this.busy.set(true);
    try {
      if (this.creating()) {
        const made = (await this.api.post<Values>(path(this.table()), body)).data;
        this.clearDraft();
        this.snack.open(this.i18n.t('form.created', { label: this.info()!.label }), undefined, {
          duration: 3000,
        });
        await this.router.navigate(['/form', this.table(), String(made['sys_id'])], {
          replaceUrl: true,
        });
      } else {
        await this.api.patch(path(this.table(), this.id()), body, etag);
        this.clearDraft();
        // Read again: display values and what may be done now come with it.
        const fresh = (await this.api.get<Values>(path(this.table(), this.id()))).data;
        this.original.set(fresh);
        this.values.set({ ...fresh });
        this.conflict.set(null);
        this.snack.open(this.i18n.t('form.saved'), undefined, { duration: 2500 });
      }
    } catch (error) {
      this.refused(error);
    } finally {
      this.busy.set(false);
    }
  }

  private refused(error: unknown): void {
    if (error instanceof Problem && error.status === 412 && error.body['current']) {
      this.showConflict(error.body['current'] as Values);
      return;
    }
    if (error instanceof Problem && error.errors.length) {
      this.errors.set(Object.fromEntries(error.errors.map((e) => [e.field, e.message])));
    }
    this.snack.open(
      describe(error, (f) => this.fields().get(f)?.label ?? f),
      this.i18n.t('common.close'),
    );
  }

  /** Someone saved in between: what they changed is taken; where both changed, the person
   * chooses. */
  private showConflict(current: Values): void {
    const mine = this.values();
    const before = this.original();
    const rows: Conflict['rows'] = [];
    const merged: Values = { ...current };
    for (const name of this.changed()) {
      const field = this.fields().get(name);
      if (!field) {
        continue;
      }
      if (same(current[name], before[name])) {
        merged[name] = mine[name];
      } else if (!same(current[name], mine[name])) {
        rows.push({
          field,
          theirs: shown(current, field, name, this.i18n.language(), (k) => this.i18n.t(k)),
          mine: shown(mine, field, name, this.i18n.language(), (k) => this.i18n.t(k)),
          keep: 'mine',
        });
      }
    }
    this.conflict.set({ current: { ...current, _merged: merged }, rows });
  }

  protected choose(index: number, keep: 'theirs' | 'mine'): void {
    this.conflict.update((c) =>
      c ? { ...c, rows: c.rows.map((r, i) => (i === index ? { ...r, keep } : r)) } : c,
    );
  }

  protected async resolve(): Promise<void> {
    const conflict = this.conflict();
    if (!conflict) {
      return;
    }
    const mine = this.values();
    const merged = { ...(conflict.current['_merged'] as Values) };
    for (const row of conflict.rows) {
      merged[row.field.name] =
        row.keep === 'mine' ? mine[row.field.name] : conflict.current[row.field.name];
    }
    const current = { ...conflict.current };
    delete current['_merged'];
    this.original.set(current);
    this.values.set(merged);
    const body = Object.fromEntries(this.changed().map((k) => [k, merged[k]]));
    if (Object.keys(body).length) {
      await this.send(body, String(current['_etag']));
    } else {
      this.conflict.set(null);
    }
  }

  protected async reload(): Promise<void> {
    this.clearDraft();
    await this.open(this.table(), this.id());
  }

  private clearDraft(): void {
    try {
      sessionStorage.removeItem(this.draftKey());
    } catch {
      // Nothing kept.
    }
    this.draft.set(null);
  }

  protected restoreDraft(): void {
    const draft = this.draft();
    if (draft) {
      this.values.update((all) => ({ ...all, ...draft }));
    }
    this.draft.set(null);
  }

  protected discardDraft(): void {
    this.clearDraft();
  }

  // ---- deleting ----

  protected async remove(): Promise<void> {
    const name = this.name() || this.info()!.label;
    const ref = this.dialog.open(ConfirmDialog, {
      data: {
        title: this.i18n.t('list.delete_title', { item: this.info()!.label.toLowerCase() }),
        text: this.i18n.t('list.delete_text', { name }),
        action: this.i18n.t('list.delete'),
      },
    });
    if (!(await firstValueFrom(ref.afterClosed()))) {
      return;
    }
    try {
      const table = this.table();
      const id = this.id();
      await this.api.delete(path(table, id), String(this.original()['_etag']));
      this.clearDraft();
      await this.back();
      const undo = this.snack.open(
        this.i18n.t('form.deleted', { name }),
        this.i18n.t('list.undo'),
        { duration: 6000 },
      );
      undo.onAction().subscribe(async () => {
        await this.api.post(`${path(table, id)}/restore`);
        await this.router.navigate(['/form', table, id]);
      });
    } catch (error) {
      this.refused(error);
    }
  }

  protected async restore(): Promise<void> {
    try {
      await this.api.post(`${path(this.table(), this.id())}/restore`);
      await this.open(this.table(), this.id());
    } catch (error) {
      this.refused(error);
    }
  }

  protected back(): Promise<boolean> {
    if (window.history.length > 1 && document.referrer !== '') {
      window.history.back();
      return Promise.resolve(true);
    }
    return this.router.navigate(['/list', this.table()]);
  }

  @HostListener('window:beforeunload', ['$event'])
  protected leaving(event: BeforeUnloadEvent): void {
    if (this.changed().length) {
      event.preventDefault();
    }
  }
}
