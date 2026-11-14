import { Component, computed, input, model } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** A day, or a day and a time (shown in the person's own time, stored with its offset). */
@Component({
  selector: 'app-date-editor',
  imports: [MatFormFieldModule, MatInputModule],
  template: `
    <mat-form-field appearance="outline" class="field">
      <mat-label>{{ field().label }}</mat-label>
      <input
        matInput
        [type]="timed() ? 'datetime-local' : 'date'"
        [required]="field().required"
        [value]="text()"
        (change)="typed($any($event.target).value)"
      />
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
export class DateEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly timed = computed(() => this.field().type === 'datetime');

  protected text(): string {
    const value = this.value();
    if (typeof value !== 'string' || !value) {
      return '';
    }
    if (!this.timed()) {
      return value.slice(0, 10);
    }
    const d = new Date(value);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  protected typed(text: string): void {
    if (!text) {
      this.value.set(null);
    } else if (this.timed()) {
      this.value.set(new Date(text).toISOString());
    } else {
      this.value.set(text);
    }
  }
}
