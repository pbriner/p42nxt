import { Component, inject, input, model, signal } from '@angular/core';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { I18n } from '../core/i18n';
import { Named, Tables } from '../core/tables';

/** Choosing a record of a table by typing part of its name. */
@Component({
  selector: 'app-reference-picker',
  imports: [MatAutocompleteModule, MatFormFieldModule, MatInputModule],
  template: `
    <mat-form-field appearance="outline" class="field">
      <mat-label>{{ label() }}</mat-label>
      <input
        matInput
        [matAutocomplete]="auto"
        [value]="text()"
        [placeholder]="i18n.t('filter.search_ref')"
        (input)="typed($any($event.target).value)"
        (focus)="typed(text())"
      />
      <mat-autocomplete #auto="matAutocomplete" (optionSelected)="picked($event.option.value)">
        @for (found of options(); track found.sys_id) {
          <mat-option [value]="found">{{ found.name }}</mat-option>
        } @empty {
          @if (searched()) {
            <mat-option disabled>{{ i18n.t('filter.nothing') }}</mat-option>
          }
        }
      </mat-autocomplete>
    </mat-form-field>
  `,
  styles: `
    .field {
      width: 100%;
    }
  `,
})
export class ReferencePicker {
  protected readonly i18n = inject(I18n);
  private readonly tables = inject(Tables);
  readonly table = input.required<string>();
  readonly label = input('');
  /** The chosen record's ID. */
  readonly value = model<string | null>(null);
  protected readonly text = signal('');
  protected readonly options = signal<Named[]>([]);
  protected readonly searched = signal(false);
  private timer: ReturnType<typeof setTimeout> | null = null;

  protected typed(text: string): void {
    this.text.set(text);
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(async () => {
      try {
        this.options.set(await this.tables.find(this.table(), text.trim()));
      } catch {
        this.options.set([]);
      }
      this.searched.set(true);
    }, 200);
  }

  protected picked(found: Named): void {
    this.text.set(found.name);
    this.value.set(found.sys_id);
  }
}
