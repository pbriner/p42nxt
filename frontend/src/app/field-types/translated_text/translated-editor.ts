import { Component, input, model } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

const LANGUAGES = [
  { code: 'en', name: 'English' },
  { code: 'de', name: 'Deutsch' },
];

/** A text in each language; English is the one every other falls back to. */
@Component({
  selector: 'app-translated-editor',
  imports: [MatFormFieldModule, MatInputModule],
  template: `
    <fieldset class="group">
      <legend>{{ field().label }}</legend>
      @for (l of languages; track l.code) {
        <mat-form-field appearance="outline" class="field">
          <mat-label>{{ l.name }}</mat-label>
          <input
            matInput
            [required]="field().required && l.code === 'en'"
            [value]="text(l.code)"
            (input)="typed(l.code, $any($event.target).value)"
          />
        </mat-form-field>
      }
      @if (error()) {
        <p class="error-text">{{ error() }}</p>
      }
    </fieldset>
  `,
  styles: `
    .group {
      border: none;
      padding: 0;
      margin: 0;
    }
    legend {
      font: var(--mat-sys-label-medium);
      color: var(--mat-sys-on-surface-variant);
      margin-bottom: 4px;
    }
    .field {
      width: 100%;
    }
  `,
})
export class TranslatedEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly languages = LANGUAGES;

  private texts(): Record<string, string> {
    const value = this.value();
    if (value && typeof value === 'object') {
      return value as Record<string, string>;
    }
    return typeof value === 'string' ? { en: value } : {};
  }

  protected text(language: string): string {
    return this.texts()[language] ?? '';
  }

  protected typed(language: string, text: string): void {
    const texts = { ...this.texts(), [language]: text };
    if (!text) {
      delete texts[language];
    }
    this.value.set(Object.keys(texts).length ? texts : null);
  }
}
