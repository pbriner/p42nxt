import { Component, computed, input, model } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { FieldNameEditor } from './field-name-editor';
import { FieldEditor } from '../unit';

const INPUT_TYPES: Record<string, string> = { email: 'email', url: 'url', phone: 'tel' };

/** One line of text: names, addresses, codes. */
@Component({
  selector: 'app-text-editor',
  imports: [FieldNameEditor, MatFormFieldModule, MatInputModule],
  template: `
    @if (field().options['field_of']) {
      <!-- A field's name, from the fields of the list another field names. -->
      <app-field-name-editor
        [field]="field()"
        [(value)]="value"
        [error]="error()"
        [shown]="shown()"
      />
    } @else {
      <mat-form-field appearance="outline" class="field" [class.mat-form-field-invalid]="!!error()">
        <mat-label>{{ field().label }}</mat-label>
        <input
          matInput
          [type]="type()"
          [required]="field().required"
          [attr.maxlength]="field().options['max_length'] ?? null"
          [value]="value() ?? ''"
          (input)="value.set($any($event.target).value || null)"
        />
        @if (error()) {
          <mat-hint class="field-error" role="alert">{{ error() }}</mat-hint>
        } @else if (field().help) {
          <mat-hint>{{ field().help }}</mat-hint>
        }
      </mat-form-field>
    }
  `,
  styles: `
    .field {
      width: 100%;
    }
  `,
  host: { '[class.invalid]': '!!error()' },
})
export class TextEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly type = computed(() => INPUT_TYPES[this.field().type] ?? 'text');
}
