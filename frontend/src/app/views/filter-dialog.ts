import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import {
  Condition,
  NO_VALUE,
  RELATIVE_DATES,
  operatorKey,
  operators,
  relative,
} from '../core/filters';
import { FieldInfo } from '../core/format';
import { I18n } from '../core/i18n';
import { ReferencePicker } from './reference-picker';

export interface FilterRequest {
  fields: FieldInfo[];
  /** Only this field (the column menu's "Filter on …"). */
  only?: string;
}

const ME = '@me';
const MY_GROUPS = '@my_groups';
const PICK_DATE = 'pick';

/** One condition: a field, what to check, and the value; it becomes one chip. */
@Component({
  selector: 'app-filter-dialog',
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    ReferencePicker,
  ],
  templateUrl: './filter-dialog.html',
  styles: `
    .field {
      display: block;
      width: min(380px, 80vw);
    }
    .pair {
      display: flex;
      gap: 8px;
      align-items: baseline;
      width: min(380px, 80vw);
      mat-form-field {
        flex: 1;
        min-width: 0;
      }
    }
  `,
})
export class FilterDialog {
  protected readonly i18n = inject(I18n);
  protected readonly data = inject<FilterRequest>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<FilterDialog, Condition>);
  protected readonly fields = this.data.fields.filter(
    (f) => f.read && f.kind !== null && (!this.data.only || f.name === this.data.only),
  );
  protected readonly field = signal<FieldInfo | null>(
    this.fields.length === 1 ? this.fields[0]! : null,
  );
  protected readonly op = signal<string | null>(null);
  protected readonly relatives = RELATIVE_DATES;
  protected value = '';
  protected second = '';
  protected values: string[] = [];
  protected dateChoice = PICK_DATE;
  protected secondChoice = PICK_DATE;
  protected readonly pick = PICK_DATE;

  /** The conditions offered; for people and groups also "me" and "my groups". */
  protected readonly ops = computed(() => {
    const field = this.field();
    if (!field) {
      return [];
    }
    const found = operators(field);
    if (field.kind === 'reference' && field.options.table === 'sys.person') {
      found.splice(1, 0, 'is_me');
    }
    if (field.kind === 'reference' && field.options.table === 'sys.group') {
      found.splice(1, 0, 'my_groups');
    }
    return found;
  });

  protected opLabel(op: string): string {
    if (op === 'is_me' || op === 'my_groups') {
      return this.i18n.t(op === 'is_me' ? 'op.is_me' : 'op.my_groups');
    }
    return this.i18n.t(operatorKey(op, this.field()?.kind ?? null));
  }

  protected relativeLabel(value: string): string {
    return relative(value, (k, p) => this.i18n.t(k, p)) ?? value;
  }

  protected chooseField(field: FieldInfo): void {
    this.field.set(field);
    this.op.set(this.ops()[0] ?? null);
    this.value = '';
    this.second = '';
    this.values = [];
  }

  protected needsValue(): boolean {
    const op = this.op();
    return !!op && !NO_VALUE.has(op) && op !== 'is_me' && op !== 'my_groups';
  }

  protected dated(): boolean {
    const kind = this.field()?.kind;
    return kind === 'date' || kind === 'datetime';
  }

  private date(choice: string, picked: string): string {
    return choice === PICK_DATE ? picked : choice;
  }

  /** The condition, or null while it isn't complete. */
  protected condition(): Condition | null {
    const field = this.field();
    const op = this.op();
    if (!field || !op) {
      return null;
    }
    if (op === 'is_me') {
      return { field: field.name, op: 'eq', value: ME };
    }
    if (op === 'my_groups') {
      return { field: field.name, op: 'in', value: [MY_GROUPS] };
    }
    if (NO_VALUE.has(op)) {
      return { field: field.name, op };
    }
    if (field.kind === 'choice') {
      return this.values.length ? { field: field.name, op, value: [...this.values] } : null;
    }
    const first = this.dated() ? this.date(this.dateChoice, this.value) : this.value.trim();
    if (!first) {
      return null;
    }
    if (op === 'between') {
      const last = this.dated() ? this.date(this.secondChoice, this.second) : this.second.trim();
      return last ? { field: field.name, op, value: [first, last] } : null;
    }
    if (field.type === 'percent') {
      return { field: field.name, op, value: String(Number(first) / 100) };
    }
    return { field: field.name, op, value: first };
  }

  protected add(): void {
    const condition = this.condition();
    if (condition) {
      this.ref.close(condition);
    }
  }
}
