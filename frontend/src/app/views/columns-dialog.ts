import {
  CdkDrag,
  CdkDragDrop,
  CdkDragHandle,
  CdkDropList,
  moveItemInArray,
} from '@angular/cdk/drag-drop';
import { Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';

import { FieldInfo } from '../core/format';
import { I18n } from '../core/i18n';

interface Column {
  name: string;
  label: string;
  shown: boolean;
}

export interface ColumnsRequest {
  shown: string[];
  available: FieldInfo[];
}

/** Which columns, in which order: by dragging, or with the arrows from the keyboard. */
@Component({
  selector: 'app-columns-dialog',
  imports: [
    CdkDrag,
    CdkDragHandle,
    CdkDropList,
    MatButtonModule,
    MatCheckboxModule,
    MatDialogModule,
  ],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('columns.title') }}</h2>
    <mat-dialog-content>
      <ul class="list" cdkDropList (cdkDropListDropped)="dropped($event)">
        @for (c of columns(); track c.name; let i = $index, last = $last) {
          <li class="row" cdkDrag>
            <span
              class="material-symbols-outlined handle"
              cdkDragHandle
              [attr.aria-label]="i18n.t('columns.drag')"
              role="img"
              >drag_indicator</span
            >
            <mat-checkbox [checked]="c.shown" (change)="toggle(i, $event.checked)">{{
              c.label
            }}</mat-checkbox>
            <span class="spacer"></span>
            <button
              mat-icon-button
              type="button"
              [disabled]="i === 0"
              [attr.aria-label]="i18n.t('columns.up', { label: c.label })"
              (click)="move(i, -1)"
            >
              <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
            </button>
            <button
              mat-icon-button
              type="button"
              [disabled]="last"
              [attr.aria-label]="i18n.t('columns.down', { label: c.label })"
              (click)="move(i, 1)"
            >
              <span class="material-symbols-outlined" aria-hidden="true">arrow_downward</span>
            </button>
          </li>
        }
      </ul>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
      <button
        mat-flat-button
        type="button"
        [disabled]="!chosen().length"
        [mat-dialog-close]="chosen()"
      >
        {{ i18n.t('columns.apply') }}
      </button>
    </mat-dialog-actions>
  `,
  styles: `
    .list {
      list-style: none;
      margin: 0;
      padding: 0;
      width: min(420px, 80vw);
    }
    .row {
      display: flex;
      align-items: center;
      gap: 4px;
      margin: 4px 0;
      padding-left: 4px;
      border-radius: var(--app-radius-small);
      background: var(--mat-sys-surface-container-high);
    }
    .handle {
      cursor: grab;
      color: var(--mat-sys-on-surface-variant);
    }
    .spacer {
      flex: 1;
    }
    .cdk-drag-preview {
      box-shadow: var(--mat-sys-level3);
    }
  `,
})
export class ColumnsDialog {
  protected readonly i18n = inject(I18n);
  private readonly data = inject<ColumnsRequest>(MAT_DIALOG_DATA);
  protected readonly columns = signal<Column[]>([
    ...this.data.shown.map((name) => ({
      name,
      label: this.data.available.find((f) => f.name === name)?.label ?? name,
      shown: true,
    })),
    ...this.data.available
      .filter((f) => !this.data.shown.includes(f.name))
      .map((f) => ({ name: f.name, label: f.label, shown: false })),
  ]);

  protected chosen(): string[] {
    return this.columns()
      .filter((c) => c.shown)
      .map((c) => c.name);
  }

  protected toggle(index: number, shown: boolean): void {
    this.columns.update((all) => all.map((c, i) => (i === index ? { ...c, shown } : c)));
  }

  protected move(index: number, by: number): void {
    this.columns.update((all) => {
      const copy = [...all];
      moveItemInArray(copy, index, index + by);
      return copy;
    });
  }

  protected dropped(event: CdkDragDrop<Column[]>): void {
    this.move(event.previousIndex, event.currentIndex - event.previousIndex);
  }
}
