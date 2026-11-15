import { Component, computed, inject, input, model, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { I18n } from '../../core/i18n';
import { Language, Languages } from '../../core/languages';
import { FieldEditor } from '../unit';

/** A text in each language. English (the source every other falls back to) and the person's
 * own language show; the other languages only when asked for. */
@Component({
  selector: 'app-translated-editor',
  imports: [MatButtonModule, MatFormFieldModule, MatInputModule],
  template: `
    <fieldset class="group">
      <legend>{{ field().label }}</legend>
      @for (l of shownLanguages(); track l.code) {
        <mat-form-field
          appearance="outline"
          class="field"
          [class.mat-form-field-invalid]="!!error()"
        >
          <mat-label>{{ l.name }}</mat-label>
          <input
            matInput
            [required]="field().required && l.code === 'en'"
            [value]="text(l.code)"
            (input)="typed(l.code, $any($event.target).value)"
          />
        </mat-form-field>
      }
      @if (hidden().length) {
        <button mat-button type="button" class="others" (click)="showAll.set(!showAll())">
          {{
            showAll()
              ? i18n.t('translated.fewer')
              : i18n.t('translated.others', { n: hidden().length })
          }}
        </button>
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
  protected readonly i18n = inject(I18n);
  private readonly languages = inject(Languages);
  protected readonly showAll = signal(false);
  private readonly mine = computed(() => {
    const own = this.i18n.language();
    return (l: Language) => l.code === 'en' || l.code === own || !!this.text(l.code);
  });
  protected readonly hidden = computed(() =>
    this.languages.all().filter((l) => l.offered && !this.mine()(l)),
  );
  protected readonly shownLanguages = computed(() =>
    this.languages.all().filter((l) => this.mine()(l) || (l.offered && this.showAll())),
  );

  constructor() {
    this.languages.load();
  }

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
