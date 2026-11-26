import { Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

import { ActionDef } from '../core/actions';
import { FieldInfo, textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { FieldHost } from '../field-types/field-host';

const KINDS: Record<string, string> = {
  string: 'text',
  text: 'text',
  choice: 'choice',
  boolean: 'boolean',
  date: 'date',
  integer: 'number',
  reference: 'reference',
};

/** What an action asks for before it runs, with the same editors as forms. */
@Component({
  selector: 'app-action-dialog',
  imports: [FieldHost, MatButtonModule, MatDialogModule],
  template: `
    <h2 mat-dialog-title>{{ title }}</h2>
    <form novalidate (submit)="$event.preventDefault(); done()">
      <mat-dialog-content class="content">
        @if (question) {
          <p>{{ question }}</p>
        }
        @for (f of fields; track f.name) {
          <app-field
            [field]="f"
            [value]="values()[f.name] ?? null"
            (changed)="set(f.name, $event)"
          />
        }
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
        <button mat-flat-button type="submit" [disabled]="!complete()">{{ title }}</button>
      </mat-dialog-actions>
    </form>
  `,
  styles: `
    .content {
      width: min(420px, 80vw);
    }
  `,
})
export class ActionDialog {
  protected readonly i18n = inject(I18n);
  private readonly action = inject<ActionDef>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<ActionDialog, Record<string, unknown>>);
  protected readonly title = textIn(this.action.label, this.i18n.language());
  protected readonly question = textIn(this.action.confirm, this.i18n.language());
  protected readonly values = signal<Record<string, unknown>>({});
  protected readonly fields: FieldInfo[] = (this.action.inputs ?? []).map((i) => ({
    name: i.name,
    type: i.type,
    kind: KINDS[i.type] ?? 'text',
    label: textIn(i.label, this.i18n.language()),
    help: null,
    required: i.required,
    default: null,
    system: false,
    read: true,
    create: true,
    write: true,
    options: i.options as FieldInfo['options'],
  }));

  protected set(name: string, value: unknown): void {
    this.values.update((all) => ({ ...all, [name]: value }));
  }

  protected complete(): boolean {
    return this.fields.every((f) => !f.required || (this.values()[f.name] ?? '') !== '');
  }

  protected done(): void {
    if (this.complete()) {
      this.ref.close(this.values());
    }
  }
}
