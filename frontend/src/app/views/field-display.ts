import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { FieldInfo, shown } from '../core/format';
import { I18n } from '../core/i18n';
import { unitOf } from '../field-types/registry';

/** A value that can't be changed here: its label over the value, links where they lead. */
@Component({
  selector: 'app-field-display',
  imports: [RouterLink],
  template: `
    <div class="display">
      <div class="label">{{ field().label }}</div>
      @if (field().type === 'json' && value() !== null && value() !== undefined) {
        <pre class="json">{{ json() }}</pre>
      } @else if (link(); as href) {
        @if (href.startsWith('/')) {
          <a class="value" [routerLink]="href">{{ text() }}</a>
        } @else {
          <a class="value" [href]="href" target="_blank" rel="noopener noreferrer">{{ text() }}</a>
        }
      } @else {
        <div class="value">{{ text() }}</div>
      }
    </div>
  `,
  styles: `
    .display {
      padding: 4px 4px 20px;
    }
    .label {
      font: var(--mat-sys-label-medium);
      color: var(--mat-sys-on-surface-variant);
    }
    .value {
      font: var(--mat-sys-body-large);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      display: block;
    }
    .json {
      font-family: var(--app-mono);
      max-height: 360px;
      overflow: auto;
      padding: 8px;
      background: var(--mat-sys-surface-container);
      border-radius: var(--app-radius-small);
    }
  `,
})
export class FieldDisplay {
  private readonly i18n = inject(I18n);
  readonly field = input.required<FieldInfo>();
  readonly record = input.required<Record<string, unknown>>();
  protected readonly value = computed(() => this.record()[this.field().name]);
  protected readonly text = computed(() =>
    shown(this.record(), this.field(), this.field().name, this.i18n.language(), (k) =>
      this.i18n.t(k),
    ),
  );
  protected readonly json = computed(() => JSON.stringify(this.value(), null, 2));
  protected readonly link = computed(() => {
    const value = this.value();
    const state = (this.record()['_reference_state'] as Record<string, string> | undefined)?.[
      this.field().name
    ];
    // A record the person can't open: no way there.
    return value === null || value === undefined || state === 'hidden'
      ? null
      : (unitOf(this.field().type).link?.(value, this.field()) ?? null);
  });
}
