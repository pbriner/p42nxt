import { Component, input, model } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

/** One of the declared values. */
@Component({
  selector: 'app-choice-editor',
  imports: [MatFormFieldModule, MatSelectModule],
  template: `
    <mat-form-field appearance="outline" class="field" [class.mat-form-field-invalid]="!!error()">
      <mat-label>{{ field().label }}</mat-label>
      <mat-select
        [required]="field().required"
        [value]="value() ?? null"
        (selectionChange)="value.set($event.value)"
      >
        @if (!field().required) {
          <mat-option [value]="null">—</mat-option>
        }
        @for (v of field().options.values ?? []; track v.value) {
          <mat-option [value]="v.value">{{ v.label }}</mat-option>
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
})
export class ChoiceEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
}
