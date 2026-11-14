import { Component, effect, inject, input, model, signal } from '@angular/core';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { I18n } from '../../core/i18n';
import { Named, Tables } from '../../core/tables';
import { FieldEditor } from '../unit';

const ICONS: Record<string, string> = {
  'sys.person': 'person',
  'sys.group': 'group',
  'sys.role': 'badge',
};

/** Another record, found by typing part of its name; text not picked goes back on leaving. */
@Component({
  selector: 'app-reference-editor',
  imports: [MatAutocompleteModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  template: `
    <mat-form-field appearance="outline" class="field">
      <mat-label>{{ field().label }}</mat-label>
      <span matIconPrefix class="material-symbols-outlined prefix" aria-hidden="true">{{
        icon()
      }}</span>
      <input
        matInput
        [required]="field().required"
        [matAutocomplete]="auto"
        [value]="text()"
        [placeholder]="i18n.t('filter.search_ref')"
        (input)="typed($any($event.target).value)"
        (focus)="typed(text())"
        (blur)="left()"
      />
      @if (value()) {
        <button
          matIconSuffix
          mat-icon-button
          type="button"
          [attr.aria-label]="i18n.t('nav.clear')"
          (click)="clear()"
        >
          <span class="material-symbols-outlined" aria-hidden="true">close</span>
        </button>
      }
      <mat-autocomplete #auto="matAutocomplete" (optionSelected)="picked($event.option.value)">
        @for (found of options(); track found.sys_id) {
          <mat-option [value]="found">{{ found.name }}</mat-option>
        } @empty {
          @if (searched()) {
            <mat-option disabled>{{ i18n.t('filter.nothing') }}</mat-option>
          }
        }
      </mat-autocomplete>
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
    .prefix {
      padding: 0 4px 0 12px;
      color: var(--mat-sys-on-surface-variant);
    }
  `,
})
export class ReferenceEditor implements FieldEditor {
  protected readonly i18n = inject(I18n);
  private readonly tables = inject(Tables);
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly text = signal('');
  protected readonly options = signal<Named[]>([]);
  protected readonly searched = signal(false);
  private name = '';
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      this.name = this.shown() ?? (this.value() ? String(this.value()) : '');
      this.text.set(this.name);
    });
  }

  protected icon(): string {
    return ICONS[String(this.field().options.table)] ?? 'link';
  }

  protected typed(text: string): void {
    this.text.set(text);
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(async () => {
      try {
        this.options.set(await this.tables.find(String(this.field().options.table), text.trim()));
      } catch {
        this.options.set([]);
      }
      this.searched.set(true);
    }, 200);
  }

  protected picked(found: Named): void {
    this.name = found.name;
    this.text.set(found.name);
    this.value.set(found.sys_id);
  }

  /** Typed text that wasn't picked goes back to the chosen record's name. */
  protected left(): void {
    setTimeout(() => this.text.set(this.name), 150);
  }

  protected clear(): void {
    this.name = '';
    this.text.set('');
    this.value.set(null);
  }
}
