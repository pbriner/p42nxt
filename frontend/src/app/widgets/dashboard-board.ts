import {
  CdkDrag,
  CdkDragDrop,
  CdkDragHandle,
  CdkDragPlaceholder,
  CdkDropList,
  moveItemInArray,
} from '@angular/cdk/drag-drop';
import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom, map } from 'rxjs';

import { describe } from '../core/api';
import { Board, Dashboards, Placed, Size, WidgetDef } from '../core/dashboards';
import { I18n } from '../core/i18n';
import { Navigation } from '../core/navigation';
import { Named, Tables } from '../core/tables';
import { ConfirmDialog } from '../views/confirm-dialog';
import { AddWidgetDialog, PickRequest } from './add-widget-dialog';
import { WidgetCard } from './widget-card';

/** A dashboard: the person's widgets in a grid, arranged by them (Customize). */
@Component({
  selector: 'app-dashboard-board',
  imports: [
    CdkDrag,
    CdkDragHandle,
    CdkDragPlaceholder,
    CdkDropList,
    MatButtonModule,
    MatFormFieldModule,
    MatProgressBarModule,
    MatSelectModule,
    MatTooltipModule,
    WidgetCard,
  ],
  templateUrl: './dashboard-board.html',
  styleUrl: './dashboard-board.scss',
})
export class DashboardBoard {
  protected readonly i18n = inject(I18n);
  private readonly dashboards = inject(Dashboards);
  private readonly navigation = inject(Navigation);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly tables = inject(Tables);
  private readonly router = inject(Router);
  private readonly query = toSignal(inject(ActivatedRoute).queryParamMap);
  readonly name = input.required<string>();
  readonly title = input.required<string>();

  protected readonly board = signal<Board | null>(null);
  protected readonly editing = signal(false);
  protected readonly draft = signal<Placed[]>([]);
  protected readonly busy = signal(false);
  protected readonly narrow = toSignal(
    inject(BreakpointObserver)
      .observe('(max-width: 719px)')
      .pipe(map((s) => s.matches)),
    { initialValue: window.matchMedia('(max-width: 719px)').matches },
  );

  private readonly defs = computed(
    () => new Map((this.board()?.widgets ?? []).map((w) => [w.name, w])),
  );
  protected readonly items = computed(() =>
    this.editing() ? this.draft() : (this.board()?.items ?? []),
  );
  protected readonly fresh = computed(() => this.board()?.new ?? []);

  /** What the placed widgets need chosen (a plan): offered above them, kept in the address. */
  protected readonly choices = computed(() => {
    const found = new Map<string, string>();
    for (const item of this.items()) {
      const w = this.def(item.widget);
      if (w?.param && w.param_table) {
        found.set(w.param, w.param_table);
      }
    }
    return [...found].map(([param, table]) => ({ param, table }));
  });
  protected readonly options = signal<Record<string, { label: string; records: Named[] }>>({});
  /** The chosen records: from the address, else the first one offered. */
  protected readonly params = computed(() => {
    const chosen: Record<string, string> = {};
    for (const { param } of this.choices()) {
      const value = this.query()?.get(param) ?? this.options()[param]?.records[0]?.sys_id;
      if (value) {
        chosen[param] = value;
      }
    }
    return chosen;
  });

  constructor() {
    effect(() => {
      const name = this.name();
      untracked(() => void this.load(name));
    });
    effect(() => {
      const wanted = this.choices();
      untracked(() => void this.offer(wanted));
    });
  }

  private async offer(wanted: { param: string; table: string }[]): Promise<void> {
    const found: Record<string, { label: string; records: Named[] }> = {};
    for (const { param, table } of wanted) {
      const [info, records] = await Promise.all([
        this.tables.describe(table),
        this.tables.find(table, '', 100),
      ]);
      found[param] = { label: info.label, records };
    }
    this.options.set(found);
  }

  protected choose(param: string, value: string): void {
    void this.router.navigate([], {
      queryParams: { [param]: value },
      queryParamsHandling: 'merge',
    });
  }

  private async load(name: string): Promise<void> {
    this.busy.set(true);
    try {
      this.board.set(await this.dashboards.load(name));
      this.editing.set(false);
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    } finally {
      this.busy.set(false);
    }
  }

  protected def(name: string): WidgetDef | undefined {
    return this.defs().get(name);
  }

  protected customize(): void {
    this.draft.set([...(this.board()?.items ?? [])]);
    this.editing.set(true);
  }

  protected move(index: number, by: number): void {
    this.draft.update((all) => {
      const copy = [...all];
      moveItemInArray(copy, index, index + by);
      return copy;
    });
  }

  protected dropped(event: CdkDragDrop<Placed[]>): void {
    this.move(event.previousIndex, event.currentIndex - event.previousIndex);
  }

  protected resize(index: number, size: Size): void {
    this.draft.update((all) => all.map((item, i) => (i === index ? { ...item, size } : item)));
  }

  protected remove(index: number): void {
    this.draft.update((all) => all.filter((_, i) => i !== index));
  }

  protected sizeLabel(size: Size): string {
    return this.i18n.t(
      size === 's' ? 'dash.size_s' : size === 'wide' ? 'dash.size_wide' : 'dash.size_large',
    );
  }

  protected async add(): Promise<void> {
    if (!this.editing()) {
      this.customize();
    }
    const placed = new Set(this.draft().map((i) => i.widget));
    const modules: Record<string, string> = { sys: 'Core' };
    for (const c of this.navigation.categories()) {
      for (const m of c.modules) {
        modules[m.id] = m.name;
      }
    }
    const data: PickRequest = {
      widgets: (this.board()?.widgets ?? []).filter((w) => !placed.has(w.name)),
      fresh: this.fresh(),
      modules,
    };
    const ref = this.dialog.open(AddWidgetDialog, { data, maxWidth: '96vw' });
    const chosen = await firstValueFrom(ref.afterClosed());
    const def = chosen ? this.def(chosen) : undefined;
    if (def) {
      this.draft.update((all) => [...all, { widget: def.name, size: def.size }]);
    }
  }

  protected async save(): Promise<void> {
    try {
      const known = (this.board()?.widgets ?? []).map((w) => w.name);
      await this.dashboards.save(this.name(), this.draft(), known);
      this.snack.open(this.i18n.t('dash.saved'), undefined, { duration: 3000 });
      await this.load(this.name());
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  protected async reset(): Promise<void> {
    const ref = this.dialog.open(ConfirmDialog, {
      data: {
        title: this.i18n.t('dash.reset_title'),
        text: this.i18n.t('dash.reset_text'),
        action: this.i18n.t('dash.reset'),
      },
    });
    if (await firstValueFrom(ref.afterClosed())) {
      await this.dashboards.reset(this.name());
      await this.load(this.name());
    }
  }
}
