import {
  CdkDrag,
  CdkDragDrop,
  CdkDragHandle,
  CdkDragPlaceholder,
  CdkDropList,
  moveItemInArray,
} from '@angular/cdk/drag-drop';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import { BreakpointObserver } from '@angular/cdk/layout';
import {
  Component,
  HostListener,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
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
    CdkTrapFocus,
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

  /** What this module's own widgets need chosen (a plan): offered above them, kept in the
   * address. Other widgets about a record keep their own choice (`perItem`). */
  protected readonly choices = computed(() => {
    const found = new Map<string, { table: string; optional: boolean }>();
    for (const item of this.items()) {
      const w = this.def(item.widget);
      if (w?.param && w.param_table && this.onPage(w)) {
        // Everything is shown while nothing is picked, unless a source widget needs a record.
        const optional = (found.get(w.param)?.optional ?? true) && !w.source;
        found.set(w.param, { table: w.param_table, optional });
      }
    }
    return [...found].map(([param, c]) => ({ param, ...c }));
  });
  /** The records offered per table: for the choices above, and for each card's own. */
  protected readonly options = signal<Record<string, { label: string; records: Named[] }>>({});
  private readonly tablesOffered = computed(() => {
    const found = new Set<string>();
    for (const item of this.items()) {
      const w = this.def(item.widget);
      if (w?.param && w.param_table) {
        found.add(w.param_table);
      }
    }
    return [...found].sort();
  });
  /** The chosen records: from the address, else the first one offered (or all of them). */
  protected readonly params = computed(() => {
    const chosen: Record<string, string> = {};
    for (const { param, table, optional } of this.choices()) {
      const value =
        this.query()?.get(param) ?? (optional ? null : this.options()[table]?.records[0]?.sys_id);
      if (value) {
        chosen[param] = value;
      }
    }
    return chosen;
  });

  /** The widget shown full screen (`?focus=` in the address, so it can be linked or put on a
   * wall screen), any widget of this dashboard's, placed or not. */
  protected readonly focused = computed(() => {
    const name = this.query()?.get('focus');
    return name && !this.editing() ? (this.def(name) ?? null) : null;
  });
  /** Its choice: the page's, or for one about a record of its own, the one in the address. */
  protected readonly focusParams = computed((): Record<string, string> => {
    const w = this.focused();
    if (!w || !this.perItem(w)) {
      return this.params();
    }
    const value = this.query()?.get(w.param!);
    return value ? { [w.param!]: value } : {};
  });

  constructor() {
    effect(() => {
      const name = this.name();
      untracked(() => void this.load(name));
    });
    effect(() => {
      const wanted = this.tablesOffered();
      untracked(() => void this.offer(wanted));
    });
  }

  private async offer(wanted: string[]): Promise<void> {
    const found: Record<string, { label: string; records: Named[] }> = {};
    for (const table of wanted) {
      const [info, records] = await Promise.all([
        this.tables.describe(table),
        this.tables.find(table, '', 100),
      ]);
      found[table] = { label: info.label, records };
    }
    this.options.set(found);
  }

  /** A widget whose choice the page offers: about a record, on its own module's dashboard. */
  protected onPage(w: WidgetDef): boolean {
    return w.dashboard === this.name() && this.name() !== 'home';
  }

  /** A card about a record that keeps its own choice. */
  protected perItem(w: WidgetDef): boolean {
    return !!w.param && !!w.param_table && !this.onPage(w);
  }

  protected paramsFor(item: Placed, w: WidgetDef): Record<string, string> {
    return this.perItem(w) ? (item.params ?? {}) : this.params();
  }

  /** A card's own choice: kept with its placement (saved at once outside Customize). */
  protected async pickFor(index: number, w: WidgetDef, value: string): Promise<void> {
    const update = (all: Placed[]): Placed[] =>
      all.map((item, i) =>
        i === index ? { ...item, params: this.withChoice(item.params, w.param!, value) } : item,
      );
    if (this.editing()) {
      this.draft.update(update);
      return;
    }
    const board = this.board();
    if (!board) {
      return;
    }
    const items = update(board.items);
    this.board.set({ ...board, items });
    try {
      await this.dashboards.save(
        this.name(),
        items,
        board.widgets.map((x) => x.name),
      );
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  private withChoice(
    params: Record<string, string> | undefined,
    param: string,
    value: string,
  ): Record<string, string> {
    const { [param]: _old, ...rest } = params ?? {};
    return value ? { ...rest, [param]: value } : rest;
  }

  /** Full screen, in the address: Back returns, and the address opens it again. */
  protected focus(item: Placed, w: WidgetDef): void {
    const own = this.perItem(w) ? (item.params ?? {}) : {};
    void this.router.navigate([], {
      queryParams: { focus: w.name, ...own },
      queryParamsHandling: 'merge',
    });
  }

  protected unfocus(): void {
    const w = this.focused();
    const own = w && this.perItem(w) ? { [w.param!]: null } : {};
    void this.router.navigate([], {
      queryParams: { focus: null, ...own },
      queryParamsHandling: 'merge',
    });
  }

  @HostListener('document:keydown.escape', ['$event'])
  protected escape(event: Event): void {
    // An open dialog or menu closes first, on its own.
    if (this.focused() && !event.defaultPrevented && !this.dialog.openDialogs.length) {
      this.unfocus();
    }
  }

  /** `''`: all of them (the choice leaves the address). */
  protected choose(param: string, value: string): void {
    void this.router.navigate([], {
      queryParams: { [param]: value || null },
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
    // One about a record may be placed again, for another record.
    const placed = new Set(
      this.draft()
        .filter((i) => {
          const w = this.def(i.widget);
          return !w || !this.perItem(w);
        })
        .map((i) => i.widget),
    );
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
