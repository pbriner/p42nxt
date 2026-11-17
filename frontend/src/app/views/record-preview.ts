import { Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { Router } from '@angular/router';

import { Api, describe } from '../core/api';
import { FieldInfo, TableInfo, shown, textIn } from '../core/format';
import { formViewOf } from '../core/form-views';
import { I18n } from '../core/i18n';
import { Tables, path } from '../core/tables';
import { FieldDisplay } from './field-display';

export interface PreviewOf {
  table: string;
  id: string;
}

interface Section {
  title: string;
  fields: FieldInfo[];
}

/** Another record, read only, without leaving the form: Open goes to its own page. */
@Component({
  selector: 'app-record-preview',
  imports: [MatButtonModule, MatDialogModule, MatProgressBarModule, FieldDisplay],
  template: `
    <h2 mat-dialog-title>{{ title() }}</h2>
    <mat-dialog-content>
      @if (busy()) {
        <mat-progress-bar mode="indeterminate" />
      } @else if (failed(); as why) {
        <p role="alert">{{ why }}</p>
      } @else {
        @for (section of sections(); track $index) {
          @if (sections().length > 1 && section.title) {
            <h3 class="section">{{ section.title }}</h3>
          }
          <div class="fields">
            @for (f of section.fields; track f.name) {
              <app-field-display [field]="f" [record]="record()" />
            }
          </div>
        }
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.close') }}</button>
      @if (!failed()) {
        <button mat-flat-button type="button" (click)="open()">
          {{ i18n.t('preview.open') }}
        </button>
      }
    </mat-dialog-actions>
  `,
  styles: `
    .fields {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      column-gap: 16px;
    }
    .section {
      font: var(--mat-sys-title-small);
      margin: 8px 0 4px;
    }
  `,
})
export class RecordPreview {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  private readonly router = inject(Router);
  private readonly ref = inject(MatDialogRef<RecordPreview>);
  private readonly of = inject<PreviewOf>(MAT_DIALOG_DATA);

  protected readonly busy = signal(true);
  protected readonly failed = signal<string | null>(null);
  protected readonly title = signal('');
  protected readonly record = signal<Record<string, unknown>>({});
  protected readonly sections = signal<Section[]>([]);

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const info = await this.tables.describe(this.of.table);
      this.title.set(info.label);
      const [record, form] = await Promise.all([
        this.api.get<Record<string, unknown>>(path(this.of.table, this.of.id)),
        formViewOf(this.api, info),
      ]);
      this.record.set(record.data);
      this.title.set(this.named(info, record.data));
      const fields = new Map(info.fields.map((f) => [f.name, f]));
      this.sections.set(
        form.sections
          .map((s) => ({
            title: textIn(s.title, this.i18n.language()),
            fields: s.fields
              .map((n) => fields.get(n))
              .filter((f): f is FieldInfo => !!f && f.read && !f.system),
          }))
          .filter((s) => s.fields.length),
      );
    } catch (error) {
      this.failed.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  private named(info: TableInfo, record: Record<string, unknown>): string {
    const display = info.fields.find((f) => f.name === info.display);
    const name = display
      ? shown(record, display, display.name, this.i18n.language(), (k) => this.i18n.t(k))
      : '';
    return `${info.label} ${name === '—' ? '' : name}`.trim();
  }

  /** Its own page; a form left with changes keeps them as a draft. */
  protected open(): void {
    this.ref.close();
    void this.router.navigate(['/form', this.of.table, this.of.id]);
  }
}
