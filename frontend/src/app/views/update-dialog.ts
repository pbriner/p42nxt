import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { FieldInfo } from '../core/format';
import { I18n } from '../core/i18n';
import { ReferencePicker } from './reference-picker';

export interface UpdateRequest {
  count: number;
  fields: FieldInfo[];
}

export interface Update {
  field: string;
  value: unknown;
}

const PLAIN = new Set([
  'string',
  'text',
  'email',
  'url',
  'phone',
  'hostname',
  'integer',
  'decimal',
  'date',
  'datetime',
]);

/** One field set to one value on every chosen record. */
@Component({
  selector: 'app-update-dialog',
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    ReferencePicker,
  ],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('update.title', { n: data.count }) }}</h2>
    <form (ngSubmit)="apply()">
      <mat-dialog-content>
        <mat-form-field appearance="outline" class="field">
          <mat-label>{{ i18n.t('update.field') }}</mat-label>
          <mat-select [value]="field()" (selectionChange)="choose($event.value)">
            @for (f of fields; track f.name) {
              <mat-option [value]="f">{{ f.label }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
        @if (field(); as f) {
          @switch (f.kind) {
            @case ('choice') {
              <mat-form-field appearance="outline" class="field">
                <mat-label>{{ i18n.t('update.value') }}</mat-label>
                <mat-select name="value" [(ngModel)]="value">
                  @if (!f.required) {
                    <mat-option [value]="null">{{ i18n.t('update.empty') }}</mat-option>
                  }
                  @for (v of f.options.values ?? []; track v.value) {
                    <mat-option [value]="v.value">{{ v.label }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
            }
            @case ('boolean') {
              <mat-form-field appearance="outline" class="field">
                <mat-label>{{ i18n.t('update.value') }}</mat-label>
                <mat-select name="value" [(ngModel)]="value">
                  <mat-option [value]="true">{{ i18n.t('common.yes') }}</mat-option>
                  <mat-option [value]="false">{{ i18n.t('common.no') }}</mat-option>
                </mat-select>
              </mat-form-field>
            }
            @case ('reference') {
              <div class="field">
                <app-reference-picker
                  [table]="$any(f.options.table)"
                  [label]="i18n.t('update.value')"
                  (valueChange)="value = $event"
                />
              </div>
            }
            @default {
              <mat-form-field appearance="outline" class="field">
                <mat-label>{{ i18n.t('update.value') }}</mat-label>
                <input matInput name="value" [type]="inputType(f)" [(ngModel)]="value" />
              </mat-form-field>
            }
          }
        }
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
        <button
          mat-flat-button
          type="submit"
          [disabled]="!field() || (field()!.required && (value === null || value === ''))"
        >
          {{ i18n.t('update.apply') }}
        </button>
      </mat-dialog-actions>
    </form>
  `,
  styles: `
    .field {
      display: block;
      width: min(380px, 80vw);
    }
  `,
})
export class UpdateDialog {
  protected readonly i18n = inject(I18n);
  protected readonly data = inject<UpdateRequest>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<UpdateDialog, Update>);
  /** Fields the person may change, of types simple enough to set here. */
  protected readonly fields = this.data.fields.filter(
    (f) =>
      f.write &&
      !f.system &&
      (PLAIN.has(f.type) || ['choice', 'boolean', 'reference'].includes(f.kind ?? '')),
  );
  protected readonly field = signal<FieldInfo | null>(null);
  protected value: unknown = null;

  protected choose(field: FieldInfo): void {
    this.field.set(field);
    this.value = null;
  }

  protected inputType(field: FieldInfo): string {
    return field.kind === 'number'
      ? 'number'
      : field.type === 'date'
        ? 'date'
        : field.type === 'datetime'
          ? 'datetime-local'
          : 'text';
  }

  protected apply(): void {
    const field = this.field();
    if (field) {
      this.ref.close({ field: field.name, value: this.value === '' ? null : this.value });
    }
  }
}
