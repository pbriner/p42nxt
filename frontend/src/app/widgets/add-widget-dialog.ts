import { Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

import { WidgetDef } from '../core/dashboards';
import { I18n } from '../core/i18n';
import { fold } from '../core/navigation';

export interface PickRequest {
  widgets: WidgetDef[];
  fresh: string[];
  modules: Record<string, string>;
}

type Kind = 'all' | 'figure' | 'chart' | 'list' | 'text';

/** Choosing a widget to add: search, kinds, modules; Enter takes the first. */
@Component({
  selector: 'app-add-widget-dialog',
  imports: [MatButtonModule, MatDialogModule],
  templateUrl: './add-widget-dialog.html',
  styleUrl: './add-widget-dialog.scss',
})
export class AddWidgetDialog {
  protected readonly i18n = inject(I18n);
  protected readonly data = inject<PickRequest>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<AddWidgetDialog, string>);
  protected readonly query = signal('');
  protected readonly kind = signal<Kind>('all');
  protected readonly module = signal<string | null>(null);
  protected readonly kinds: Kind[] = ['all', 'figure', 'chart', 'list', 'text'];

  private readonly matching = computed(() => {
    const words = fold(this.query()).split(/\s+/).filter(Boolean);
    return this.data.widgets.filter((w) => {
      const text = fold(`${w.title} ${this.moduleName(w.module)}`);
      return (
        (this.kind() === 'all' || w.kind === this.kind()) &&
        words.every((word) => text.includes(word))
      );
    });
  });
  protected readonly modules = computed(() => {
    const counts = new Map<string, number>();
    for (const w of this.matching()) {
      counts.set(w.module, (counts.get(w.module) ?? 0) + 1);
    }
    return [...counts.entries()].map(([id, count]) => ({ id, name: this.moduleName(id), count }));
  });
  protected readonly groups = computed(() =>
    this.modules()
      .filter((m) => !this.module() || m.id === this.module())
      .map((m) => ({ ...m, widgets: this.matching().filter((w) => w.module === m.id) })),
  );

  protected moduleName(id: string): string {
    return this.data.modules[id] ?? id;
  }

  protected kindLabel(kind: Kind): string {
    const keys = {
      all: 'picker.all',
      figure: 'picker.figures',
      chart: 'picker.charts',
      list: 'picker.lists',
      text: 'picker.texts',
    } as const;
    return this.i18n.t(keys[kind]);
  }

  protected describe(w: WidgetDef): string {
    const kind =
      w.kind === 'chart'
        ? this.i18n.t('picker.kind_chart', { chart: w.chart ?? '' })
        : this.i18n.t(
            w.kind === 'figure'
              ? 'picker.kind_figure'
              : w.kind === 'list'
                ? 'picker.kind_list'
                : 'picker.kind_text',
          );
    const size = { s: 'dash.size_s', wide: 'dash.size_wide', large: 'dash.size_large' } as const;
    return `${kind} · ${this.i18n.t(size[w.size])}`;
  }

  protected pick(w: WidgetDef): void {
    this.ref.close(w.name);
  }

  protected first(): void {
    const found = this.groups()[0]?.widgets[0];
    if (found) {
      this.pick(found);
    }
  }
}
