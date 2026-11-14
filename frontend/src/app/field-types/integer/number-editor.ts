import { Component, computed, input, model } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

/** Numbers: whole, exact decimals (kept as text, never rounded), percent (stored as a fraction). */
@Component({
  selector: 'app-number-editor',
  imports: [MatFormFieldModule, MatInputModule],
  template: `
    <mat-form-field appearance="outline" class="field">
      <mat-label>{{ field().label }}</mat-label>
      <input
        matInput
        type="number"
        [step]="step()"
        [required]="field().required"
        [value]="text()"
        (input)="typed($any($event.target).value)"
      />
      @if (percent()) {
        <span matTextSuffix>%</span>
      }
      @if (field().help) {
        <mat-hint>{{ field().help }}</mat-hint>
      }
      @if (error()) {
        <mat-error>{{ error() }}</mat-error>
      }
    </mat-form-field>
  `,
  styles: `
    .field {
      width: 100%;
    }
  `,
})
export class NumberEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly percent = computed(() => this.field().type === 'percent');
  protected readonly step = computed(() => (this.field().type === 'integer' ? '1' : 'any'));

  protected text(): string {
    const value = this.value();
    if (value === null || value === undefined || value === '') {
      return '';
    }
    return this.percent() ? String(Math.round(Number(value) * 1e6) / 1e4) : String(value);
  }

  protected typed(text: string): void {
    if (!text.trim()) {
      this.value.set(null);
    } else if (this.percent()) {
      this.value.set(String(Number(text) / 100));
    } else if (this.field().type === 'integer') {
      this.value.set(Number(text));
    } else {
      this.value.set(text);
    }
  }
}
