import { Component, computed, input, model, signal } from '@angular/core';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { FieldInfo } from '../../core/format';
import { FieldEditor } from '../unit';

/** Several lines: long text, Markdown, JSON (edited as text, checked as it's typed), or a list
 *  of short texts (a JSON field with `lines`: one per line, no JSON to type). */
@Component({
  selector: 'app-textarea-editor',
  imports: [MatFormFieldModule, MatInputModule],
  template: `
    <mat-form-field
      appearance="outline"
      class="field"
      [class.mat-form-field-invalid]="!!(problem() ?? error())"
    >
      <mat-label>{{ field().label }}</mat-label>
      <textarea
        matInput
        [class.mono]="json() || lines() || field().type === 'markdown'"
        [rows]="json() || lines() ? 4 : 5"
        [required]="field().required"
        [value]="text()"
        (input)="typed($any($event.target).value)"
      ></textarea>
      @if (problem() ?? error(); as message) {
        <mat-hint class="field-error" role="alert">{{ message }}</mat-hint>
      } @else if (field().help) {
        <mat-hint>{{ field().help }}</mat-hint>
      }
    </mat-form-field>
  `,
  styles: `
    .field {
      width: 100%;
    }
    .mono {
      font-family: var(--app-mono);
    }
  `,
})
export class TextareaEditor implements FieldEditor {
  readonly field = input.required<FieldInfo>();
  readonly value = model<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  protected readonly lines = computed(
    () => this.field().type === 'json' && this.field().options['lines'] === true,
  );
  protected readonly json = computed(() => this.field().type === 'json' && !this.lines());
  protected readonly problem = signal<string | null>(null);
  private typedText: string | null = null;

  protected text(): string {
    if (this.typedText !== null) {
      return this.typedText;
    }
    const value = this.value();
    if (value === null || value === undefined) {
      return '';
    }
    if (this.lines()) {
      return Array.isArray(value) ? value.join('\n') : String(value);
    }
    return this.json() ? JSON.stringify(value, null, 2) : String(value);
  }

  protected typed(text: string): void {
    if (this.lines()) {
      this.typedText = text;
      const items = text
        .split(/[\n,;]+/)
        .map((item) => item.trim())
        .filter((item) => item);
      this.value.set(items.length ? items : null);
      return;
    }
    if (!this.json()) {
      this.value.set(text || null);
      return;
    }
    this.typedText = text;
    if (!text.trim()) {
      this.problem.set(null);
      this.value.set(null);
      return;
    }
    try {
      this.value.set(JSON.parse(text));
      this.problem.set(null);
    } catch {
      this.problem.set('Not valid JSON, e.g. {"status": "open"}');
    }
  }
}
