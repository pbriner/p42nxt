import {
  Component,
  computed,
  effect,
  inject,
  input,
  model,
  signal,
  untracked,
} from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';

import { FieldInfo } from '../../core/format';
import { FormValues } from '../../core/form-values';
import { ListViews } from '../../core/list-views';
import { Tables } from '../../core/tables';
import { FieldEditor } from '../unit';

/** A field's name, picked from the fields of the list another field of the record names (a
 *  widget's measured field from its list), only those of the kinds that fit. */
@Component({
  selector: 'app-field-name-editor',
  imports: [MatFormFieldModule, MatSelectModule],
  template: `
    <mat-form-field appearance="outline" class="field" [class.mat-form-field-invalid]="!!error()">
      <mat-label>{{ field().label }}</mat-label>
      <mat-select
        [value]="value() ?? null"
        [required]="field().required"
        (selectionChange)="value.set($event.value)"
      >
        <mat-option [value]="null">—</mat-option>
        @for (f of choices(); track f.name) {
          <mat-option [value]="f.name">{{ f.label }}</mat-option>
        }
        @if (value() && !known()) {
          <mat-option [value]="value()">{{ value() }}</mat-option>
        }
      </mat-select>
      @if (error()) {
        <mat-hint class="field-error" role="alert">{{ error() }}</mat-hint>
      } @else if (field().help) {
        <mat-hint>{{ field().help }}</mat-hint>
      }
    </mat-form-field>
  `,
  styles: `
    .field {
      width: 100%;
    }
  `,
  host: { '[class.invalid]': '!!error()' },
})
export class FieldNameEditor implements FieldEditor {
  private readonly form = inject(FormValues, { optional: true });
  private readonly views = inject(ListViews);
  private readonly tables = inject(Tables);
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  private readonly fields = signal<FieldInfo[]>([]);
  protected readonly choices = computed(() => {
    const kinds = this.field().options['kinds'] as string[] | undefined;
    return this.fields().filter(
      (f) => f.read && f.kind && f.name !== 'sys_id' && (!kinds || kinds.includes(f.kind)),
    );
  });
  protected readonly known = computed(() => this.choices().some((f) => f.name === this.value()));
  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const holder = String(this.field().options['field_of'] ?? '');
      const list = this.form?.values()[holder];
      untracked(() => void this.load(typeof list === 'string' ? list : null));
    });
  }

  private async load(list: string | null): Promise<void> {
    if (list === this.loadedFor) {
      return;
    }
    this.loadedFor = list;
    if (!list) {
      this.fields.set([]);
      return;
    }
    try {
      const view = await this.views.view(list);
      this.fields.set(view ? (await this.tables.describe(view.table)).fields : []);
    } catch {
      this.fields.set([]);
    }
  }
}
