import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { MatCardModule } from '@angular/material/card';

import { Size, WidgetDef } from '../core/dashboards';
import { textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { ListViews } from '../core/list-views';
import { WidgetCard } from '../widgets/widget-card';

/** A widget as it will look, from its form's values as typed, read like any widget (through
 *  the list and totals API, with the person's rights). */
@Component({
  selector: 'app-widget-preview',
  imports: [MatCardModule, WidgetCard],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('widget.preview') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        @if (shown(); as def) {
          <div class="frame" [class.wide]="def.size !== 's'">
            <app-widget-card [widget]="def" />
          </div>
        } @else {
          <p class="muted">{{ i18n.t('widget.preview_none') }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .frame {
      max-width: 360px;
    }
    .frame.wide {
      max-width: 720px;
    }
  `,
})
export class WidgetPreview {
  protected readonly i18n = inject(I18n);
  private readonly views = inject(ListViews);
  readonly values = input.required<Record<string, unknown>>();
  protected readonly shown = signal<WidgetDef | null>(null);
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    // Waits for typing to pause, so a half-typed field isn't asked for.
    effect(() => {
      const values = this.values();
      untracked(() => {
        if (this.timer) {
          clearTimeout(this.timer);
        }
        this.timer = setTimeout(() => void this.build(values), 400);
      });
    });
  }

  private async build(v: Record<string, unknown>): Promise<void> {
    const kind = v['kind'] as WidgetDef['kind'] | null;
    const list = typeof v['list'] === 'string' ? v['list'] : null;
    const view = list ? await this.views.view(list).catch(() => null) : null;
    if (!kind || (kind !== 'text' && !view)) {
      this.shown.set(null);
      return;
    }
    const text = (name: string) => (typeof v[name] === 'string' ? (v[name] as string) : null);
    let filter: unknown = v['filter'] ?? null;
    if (typeof filter === 'string') {
      try {
        filter = JSON.parse(filter);
      } catch {
        filter = null;
      }
    }
    this.shown.set({
      name: String(v['name'] ?? 'preview'),
      module: 'preview',
      dashboard: 'home',
      title: textIn(v['title'], this.i18n.language()) || this.i18n.t('widget.preview'),
      kind,
      list,
      source: null,
      param: null,
      param_table: null,
      table: view?.table ?? null,
      filter,
      measure: text('measure') ?? 'count',
      field: text('field'),
      field2: text('field2'),
      group: text('group'),
      period: text('period'),
      chart: (text('chart') as WidgetDef['chart']) ?? null,
      text: textIn(v['text'], this.i18n.language()) || null,
      icon: text('icon'),
      accent: typeof v['accent'] === 'number' ? v['accent'] : null,
      filled: !!v['filled'],
      target: v['target'] !== null && v['target'] !== undefined ? String(v['target']) : null,
      trend: text('trend'),
      dated: text('dated'),
      sizes: ['s', 'wide', 'large'],
      size: ((text('size') as Size | null) ?? 's') as Size,
      refresh: null,
      shown: 'default',
    });
  }
}
