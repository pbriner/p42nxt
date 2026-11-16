import { Component, inject, input, model, OnInit, signal } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';

import { Api } from '../../core/api';
import { FieldInfo, textIn } from '../../core/format';
import { FieldEditor } from '../unit';

interface Offered {
  code: string;
  name: string;
}

/** A currency, from those the installation offers (Currencies). */
@Component({
  selector: 'app-currency-editor',
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
        @for (c of choices(); track c.code) {
          <mat-option [value]="c.code">{{ c.code }} · {{ c.name }}</mat-option>
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
export class CurrencyEditor implements FieldEditor, OnInit {
  private readonly api = inject(Api);
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly choices = signal<Offered[]>([]);

  async ngOnInit(): Promise<void> {
    const current = this.value() ? String(this.value()) : null;
    try {
      const { data } = await this.api.get<{ records: Record<string, unknown>[] }>(
        '/api/v1/sys/currency',
        { query: { 'filter[active]': true, sort: 'name', limit: 200, fields: 'name,label' } },
      );
      const found = data.records.map((r) => ({
        code: String(r['name']),
        name: textIn(r['label'], this.api.language()),
      }));
      // A currency no longer offered stays choosable where it's already set.
      if (current && !found.some((c) => c.code === current)) {
        found.unshift({ code: current, name: current });
      }
      this.choices.set(found);
    } catch {
      this.choices.set(current ? [{ code: current, name: current }] : []);
    }
  }
}
