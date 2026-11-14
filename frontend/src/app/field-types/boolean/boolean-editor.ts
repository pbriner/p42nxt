import { Component, input, model } from '@angular/core';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

/** Yes or no. */
@Component({
  selector: 'app-boolean-editor',
  imports: [MatSlideToggleModule],
  template: `
    <div class="toggle">
      <mat-slide-toggle [checked]="!!value()" (change)="value.set($event.checked)">{{
        field().label
      }}</mat-slide-toggle>
      @if (field().help) {
        <p class="hint">{{ field().help }}</p>
      }
      @if (error()) {
        <p class="hint error-text">{{ error() }}</p>
      }
    </div>
  `,
  styles: `
    .toggle {
      padding: 12px 4px 20px;
    }
    .hint {
      margin: 4px 0 0;
      font: var(--mat-sys-body-small);
      color: var(--mat-sys-on-surface-variant);
    }
  `,
})
export class BooleanEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
}
