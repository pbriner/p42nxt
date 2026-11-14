import { Component, input, model } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

const HEX = /^#[0-9a-fA-F]{6}$/;

/** A colour as #rrggbb, typed or picked. */
@Component({
  selector: 'app-color-editor',
  imports: [MatFormFieldModule, MatInputModule],
  template: `
    <mat-form-field appearance="outline" class="field" [class.mat-form-field-invalid]="!!error()">
      <mat-label>{{ field().label }}</mat-label>
      <span
        matTextPrefix
        class="swatch"
        [style.background]="valid() ? value() : 'transparent'"
      ></span>
      <input
        matInput
        maxlength="7"
        [required]="field().required"
        [value]="value() ?? ''"
        (input)="value.set($any($event.target).value || null)"
      />
      <input
        matTextSuffix
        type="color"
        class="picker"
        [attr.aria-label]="field().label"
        [value]="valid() ? value() : '#000000'"
        (input)="value.set($any($event.target).value)"
      />
      @if (error() ?? (value() && !valid() ? 'Use the format #rrggbb' : null); as message) {
        <mat-hint class="field-error" role="alert">{{ message }}</mat-hint>
      }
    </mat-form-field>
  `,
  styles: `
    .field {
      width: 100%;
    }
    .swatch {
      display: inline-block;
      width: 14px;
      height: 14px;
      margin-right: 8px;
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 2px;
      vertical-align: middle;
    }
    .picker {
      width: 32px;
      height: 24px;
      border: none;
      background: none;
      padding: 0;
    }
  `,
})
export class ColorEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);

  protected valid(): boolean {
    return typeof this.value() === 'string' && HEX.test(this.value() as string);
  }
}
