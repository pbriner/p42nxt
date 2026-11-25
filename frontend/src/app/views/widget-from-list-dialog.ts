import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatRadioModule } from '@angular/material/radio';
import { MatSelectModule } from '@angular/material/select';

import { FieldInfo } from '../core/format';
import { I18n } from '../core/i18n';

export interface WidgetFromList {
  title: string;
  fields: FieldInfo[];
}

export interface WidgetChoice {
  title: string;
  kind: 'figure' | 'list' | 'chart';
  group: string | null;
}

/** What kind of widget a list becomes; its filter and columns come along. */
@Component({
  selector: 'app-widget-from-list-dialog',
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatRadioModule,
    MatSelectModule,
  ],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('widget.from_list') }}</h2>
    <mat-dialog-content>
      <mat-form-field appearance="outline" class="wide">
        <mat-label>{{ i18n.t('widget.title') }}</mat-label>
        <input matInput name="title" maxlength="120" [(ngModel)]="title" required />
      </mat-form-field>
      <mat-radio-group class="kinds" [(ngModel)]="kind" name="kind">
        <mat-radio-button value="figure">{{ i18n.t('widget.kind_figure') }}</mat-radio-button>
        <mat-radio-button value="list">{{ i18n.t('widget.kind_list') }}</mat-radio-button>
        <mat-radio-button value="chart">{{ i18n.t('widget.kind_chart') }}</mat-radio-button>
      </mat-radio-group>
      @if (kind === 'chart') {
        <mat-form-field appearance="outline" class="wide" floatLabel="always">
          <mat-label>{{ i18n.t('widget.grouped_by') }}</mat-label>
          <mat-select [(ngModel)]="group" name="group" required>
            @for (f of groupable; track f.name) {
              <mat-option [value]="f.name">{{ f.label }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
      <button
        mat-flat-button
        type="button"
        [disabled]="!title.trim() || (kind === 'chart' && !group)"
        [mat-dialog-close]="{ title: title.trim(), kind, group: kind === 'chart' ? group : null }"
      >
        {{ i18n.t('widget.make') }}
      </button>
    </mat-dialog-actions>
  `,
  styles: `
    .wide {
      width: min(420px, 80vw);
      display: block;
    }
    .kinds {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin: 8px 0 16px;
    }
  `,
})
export class WidgetFromListDialog {
  protected readonly i18n = inject(I18n);
  private readonly data = inject<WidgetFromList>(MAT_DIALOG_DATA);
  protected title = this.data.title;
  protected kind: WidgetChoice['kind'] = 'figure';
  protected group: string | null = null;
  protected readonly groupable = this.data.fields.filter(
    (f) => f.read && ['choice', 'reference', 'boolean', 'date', 'datetime'].includes(f.kind ?? ''),
  );
}
