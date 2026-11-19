import { Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatProgressBarModule } from '@angular/material/progress-bar';

import { Api, Problem, describe } from '../core/api';
import { FieldInfo, TableInfo, textIn } from '../core/format';
import { formViewOf } from '../core/form-views';
import { I18n } from '../core/i18n';
import { FormRule, Rules, effects } from '../core/rules';
import { Session } from '../core/session';
import { Tables, path } from '../core/tables';
import { FieldHost } from '../field-types/field-host';
import { FieldDisplay } from './field-display';

type Values = Record<string, unknown>;

export interface RecordOf {
  table: string;
  /** null: a new record. */
  id: string | null;
  /** Fields set and locked: the record it belongs to (a line's statement). */
  fixed: Record<string, string>;
  /** How the fixed fields read (the statement's name). */
  fixedNames: Record<string, string>;
}

interface Section {
  title: string;
  fields: FieldInfo[];
}

/** A record of a related list, made or changed without leaving the form it belongs to. */
@Component({
  selector: 'app-record-dialog',
  imports: [MatButtonModule, MatDialogModule, MatProgressBarModule, FieldHost, FieldDisplay],
  template: `
    <h2 mat-dialog-title>{{ title() }}</h2>
    <mat-dialog-content>
      @if (loading()) {
        <mat-progress-bar mode="indeterminate" />
      } @else if (failed(); as why) {
        <p role="alert">{{ why }}</p>
      } @else {
        <form id="record-dialog" (submit)="$event.preventDefault(); save()">
          @for (section of sections(); track $index) {
            @if (sections().length > 1 && section.title) {
              <h3 class="section">{{ section.title }}</h3>
            }
            <div class="fields">
              @for (f of section.fields; track f.name) {
                @if (!effectsNow().hidden.has(f.name)) {
                  <div class="cell">
                    @if (editable(f)) {
                      <app-field
                        [field]="withRules(f)"
                        [value]="values()[f.name] ?? null"
                        [error]="errors()[f.name] ?? null"
                        [shown]="shownOf(f)"
                        (changed)="set(f.name, $event)"
                      />
                    } @else {
                      <app-field-display [field]="f" [record]="shownRecord()" />
                    }
                  </div>
                }
              }
            </div>
          }
          <button type="submit" hidden aria-hidden="true" tabindex="-1"></button>
        </form>
        @if (errors()['_']; as why) {
          <p role="alert" class="error">{{ why }}</p>
        }
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" [mat-dialog-close]="false">
        {{ i18n.t('common.cancel') }}
      </button>
      @if (!failed() && !loading()) {
        <button mat-flat-button type="button" [disabled]="busy()" (click)="save()">
          {{ of.id ? i18n.t('form.save') : i18n.t('form.create') }}
        </button>
      }
    </mat-dialog-actions>
  `,
  styles: `
    .fields {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      column-gap: 16px;
    }
    .error {
      color: var(--mat-sys-error);
    }
    .section {
      font: var(--mat-sys-title-small);
      margin: 8px 0 4px;
    }
  `,
})
export class RecordDialog {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  private readonly rulesApi = inject(Rules);
  private readonly session = inject(Session);
  private readonly ref = inject(MatDialogRef<RecordDialog, boolean>);
  protected readonly of = inject<RecordOf>(MAT_DIALOG_DATA);

  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly failed = signal<string | null>(null);
  private readonly info = signal<TableInfo | null>(null);
  protected readonly sections = signal<Section[]>([]);
  private readonly original = signal<Values>({});
  protected readonly values = signal<Values>({});
  protected readonly errors = signal<Record<string, string>>({});
  private readonly rules = signal<FormRule[]>([]);
  protected readonly effectsNow = computed(() =>
    effects(this.rules(), this.values(), this.session.me()?.person.sys_id ?? ''),
  );
  /** The record as stored, with the fixed fields named: what read-only fields show. */
  protected readonly shownRecord = computed(() => {
    const record = this.original();
    const display = { ...(record['_display'] as Values | undefined), ...this.of.fixedNames };
    return { ...this.of.fixed, ...record, _display: display };
  });
  protected readonly title = computed(() => {
    const label = this.info()?.label ?? '';
    return this.of.id ? label : this.i18n.t('related.new_title', { item: label });
  });

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const info = await this.tables.describe(this.of.table);
      const [form, rules, record] = await Promise.all([
        formViewOf(this.api, info),
        this.rulesApi.of(this.of.table),
        this.of.id
          ? this.api.get<Values>(path(this.of.table, this.of.id)).then((a) => a.data)
          : Promise.resolve(this.defaults(info)),
      ]);
      const fields = new Map(info.fields.map((f) => [f.name, f]));
      this.info.set(info);
      this.rules.set(rules);
      this.original.set(this.of.id ? record : {});
      this.values.set({ ...record, ...this.of.fixed });
      this.sections.set(
        form.sections
          .map((s) => ({
            title: textIn(s.title, this.i18n.language()),
            fields: s.fields
              .map((n) => fields.get(n))
              .filter((f): f is FieldInfo => !!f && this.visible(f)),
          }))
          .filter((s) => s.fields.length),
      );
    } catch (error) {
      this.failed.set(describe(error));
    } finally {
      this.loading.set(false);
    }
  }

  private defaults(info: TableInfo): Values {
    const values: Values = {};
    for (const f of info.fields) {
      if (!f.system && f.create && f.default !== null && f.default !== undefined) {
        values[f.name] = f.default;
      }
    }
    return values;
  }

  private visible(f: FieldInfo): boolean {
    if (f.system) {
      return false;
    }
    return this.of.id ? f.read : f.create && !f.options['read_only'];
  }

  protected editable(f: FieldInfo): boolean {
    if (f.name in this.of.fixed || f.options['read_only']) {
      return false;
    }
    if (this.effectsNow().readOnly.has(f.name)) {
      return false;
    }
    return this.of.id ? f.write : f.create;
  }

  protected withRules(f: FieldInfo): FieldInfo {
    return this.effectsNow().required.has(f.name) && !f.required ? { ...f, required: true } : f;
  }

  protected shownOf(f: FieldInfo): string | null {
    const display = this.original()['_display'] as Values | undefined;
    const name = display?.[f.name];
    return name === undefined || name === null ? null : String(name);
  }

  protected set(name: string, value: unknown): void {
    this.values.update((v) => ({ ...v, [name]: value }));
    this.errors.update((e) => {
      const { [name]: _, ...rest } = e;
      return rest;
    });
  }

  private changed(): string[] {
    const before = this.original();
    return Object.keys(this.values()).filter(
      (k) =>
        !k.startsWith('_') &&
        (this.of.id ? before[k] !== this.values()[k] : this.values()[k] !== undefined),
    );
  }

  private missing(): boolean {
    const missing: Record<string, string> = {};
    for (const section of this.sections()) {
      for (const f of section.fields) {
        const v = this.values()[f.name];
        const needed = f.required || this.effectsNow().required.has(f.name);
        if (this.editable(f) && needed && f.type !== 'boolean' && (v ?? '') === '') {
          missing[f.name] = this.i18n.t('form.required', { label: f.label });
        }
      }
    }
    this.errors.set(missing);
    return Object.keys(missing).length > 0;
  }

  protected async save(): Promise<void> {
    if (this.busy() || this.missing()) {
      return;
    }
    const body = Object.fromEntries(this.changed().map((k) => [k, this.values()[k]]));
    this.busy.set(true);
    try {
      if (this.of.id) {
        await this.api.patch(
          path(this.of.table, this.of.id),
          body,
          String(this.original()['_etag'] ?? ''),
        );
      } else {
        await this.api.post(path(this.of.table), body);
      }
      this.ref.close(true);
    } catch (error) {
      if (error instanceof Problem && error.errors.length) {
        this.errors.set(Object.fromEntries(error.errors.map((e) => [e.field, e.message])));
      }
      this.failed.set(null);
      this.errors.update((e) =>
        Object.keys(e).length
          ? e
          : {
              _: describe(error, (f) => this.info()?.fields.find((x) => x.name === f)?.label ?? f),
            },
      );
    } finally {
      this.busy.set(false);
    }
  }
}
