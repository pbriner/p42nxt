import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { Router, RouterLink } from '@angular/router';

import { Api } from '../core/api';
import { Dashboards, WidgetDef } from '../core/dashboards';
import { TableInfo, shown } from '../core/format';
import { I18n } from '../core/i18n';
import { ListViews } from '../core/list-views';
import { filterQuery } from '../core/navigation';
import { Tables, path } from '../core/tables';
import { Chart, Point } from './chart';

const LIMITS = { bar: 8, donut: 6, column: 50, line: 50 } as const;
const RANGES = { '3m': '@today-3m', '1y': '@today-1y', all: null } as const;
type Range = keyof typeof RANGES;

/** What a source route answers: a figure, or a chart's points. */
interface SourceAnswer {
  value?: number | null;
  unit?: string;
  text?: string;
  detail?: string;
  tone?: 'good' | 'bad' | null;
  subtitle?: string;
  link?: string;
  points?: { key?: unknown; label: string; value: number }[];
}

interface Line {
  id: string;
  title: string;
  detail: string;
}

function and(...parts: unknown[]): unknown {
  const found = parts.filter((p) => p !== null && p !== undefined);
  return found.length === 0 ? null : found.length === 1 ? found[0] : { and: found };
}

/** One widget: its data read through the API as the person, so they see only what they may. */
@Component({
  selector: 'app-widget-card',
  imports: [Chart, MatButtonModule, MatCardModule, MatProgressBarModule, RouterLink],
  templateUrl: './widget-card.html',
  styleUrl: './widget-card.scss',
})
export class WidgetCard {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly dashboards = inject(Dashboards);
  private readonly views = inject(ListViews);
  private readonly tables = inject(Tables);
  private readonly router = inject(Router);
  readonly widget = input.required<WidgetDef>();
  readonly compact = input(false);
  /** What the page chose for widgets about one record (a plan). */
  readonly params = input<Record<string, string>>({});
  protected readonly unchosen = signal(false);

  protected readonly busy = signal(false);
  protected readonly failed = signal(false);
  protected readonly value = signal<number | null>(null);
  protected readonly trend = signal<number[]>([]);
  protected readonly points = signal<Point[]>([]);
  protected readonly lines = signal<Line[]>([]);
  protected readonly table = signal<TableInfo | null>(null);
  protected readonly asTable = signal(false);
  protected readonly range = signal<Range>('all');
  protected readonly answer = signal<SourceAnswer | null>(null);
  private viewFilter: unknown = null;
  private viewSort: string | null = null;

  protected readonly accent = computed(() => `var(--chart-${this.widget().accent ?? 1})`);
  protected readonly dated = computed(() => {
    if (this.widget().source) {
      return false;
    }
    const f = this.table()?.fields.find((x) => x.name === this.widget().group);
    return f?.kind === 'date' || f?.kind === 'datetime';
  });
  protected readonly subtitle = computed(() => {
    const w = this.widget();
    const info = this.table();
    if (w.source) {
      return this.answer()?.subtitle ?? info?.label_plural ?? '';
    }
    if (!info || w.kind === 'text') {
      return '';
    }
    if (w.measure === 'count') {
      const plural = info.label_plural ?? info.label;
      const total = this.value();
      return w.kind !== 'figure' && total !== null ? `${this.format(total)} ${plural}` : plural;
    }
    return info.fields.find((f) => f.name === w.field)?.label ?? '';
  });
  protected readonly target = computed(() =>
    this.widget().target === null ? null : Number(this.widget().target),
  );
  protected readonly reached = computed(() => {
    const target = Number(this.widget().target);
    const value = this.value();
    return target && value !== null ? Math.round((value / target) * 100) : null;
  });

  constructor() {
    effect(() => {
      const w = this.widget();
      this.range();
      this.params();
      untracked(() => void this.load(w));
    });
    let timer: ReturnType<typeof setInterval> | null = null;
    effect(() => {
      const every = this.widget().refresh;
      if (timer) {
        clearInterval(timer);
      }
      timer = every
        ? setInterval(() => void this.load(untracked(() => this.widget())), every * 1000)
        : null;
    });
    inject(DestroyRef).onDestroy(() => timer && clearInterval(timer));
  }

  private async load(w: WidgetDef): Promise<void> {
    if (w.kind === 'text' || !w.table || !w.list) {
      return;
    }
    this.busy.set(true);
    this.failed.set(false);
    try {
      const [info, view] = await Promise.all([
        this.tables.describe(w.table),
        this.views.view(w.list),
      ]);
      this.table.set(info);
      this.viewFilter = view?.filter ?? null;
      this.viewSort = view?.sort?.join(',') ?? null;
      if (w.source) {
        await this.fromSource(w);
      } else if (w.kind === 'figure') {
        await this.figure(w);
      } else if (w.kind === 'chart') {
        await this.chart(w, info);
      } else {
        await this.list(w, info);
      }
    } catch {
      this.failed.set(true);
    } finally {
      this.busy.set(false);
    }
  }

  private where(w: WidgetDef, ...more: unknown[]): string | null {
    const all = and(this.viewFilter, w.filter, ...more);
    return all ? JSON.stringify(all) : null;
  }

  /** A source widget: the module's route works out the figure or the points. */
  private async fromSource(w: WidgetDef): Promise<void> {
    const value = w.param ? this.params()[w.param] : null;
    this.unchosen.set(!!w.param && !value);
    if (w.param && !value) {
      return;
    }
    const { data } = await this.api.get<SourceAnswer>(w.source!, {
      query: w.param ? { [w.param]: value } : {},
    });
    this.answer.set(data);
    if (w.kind === 'figure') {
      this.value.set(typeof data.value === 'number' ? data.value : null);
      return;
    }
    const donut = w.chart === 'donut';
    this.points.set(
      (data.points ?? []).map((p, i) => ({
        key: p.key ?? p.label,
        label: p.label,
        value: Number(p.value ?? 0),
        // One series in the widget's accent; a donut's slices each their own colour.
        color: donut ? `var(--chart-${(i % 8) + 1})` : this.accent(),
        link: false,
      })),
    );
    this.value.set(null);
  }

  private async figure(w: WidgetDef): Promise<void> {
    const totals = await this.dashboards.totals(w.table!, {
      measure: w.measure,
      field: w.field,
      where: this.where(w),
    });
    this.value.set(totals.total === null ? null : Number(totals.total));
    if (w.trend) {
      const months = await this.dashboards.totals(w.table!, {
        measure: w.measure,
        field: w.field,
        group: w.trend,
        period: 'month',
        limit: 13,
        where: this.where(w, { field: w.trend, op: 'gte', value: '@today-1y' }),
      });
      this.trend.set(months.groups.map((g) => Number(g.value ?? 0)));
    }
  }

  private async chart(w: WidgetDef, info: TableInfo): Promise<void> {
    const kind = w.chart ?? 'bar';
    const since = RANGES[this.range()];
    const field = info.fields.find((f) => f.name === w.group);
    const dated = field?.kind === 'date' || field?.kind === 'datetime';
    const totals = await this.dashboards.totals(w.table!, {
      measure: w.measure,
      field: w.field,
      group: w.group,
      period: w.period,
      limit: LIMITS[kind],
      where: this.where(w, dated && since ? { field: w.group, op: 'gte', value: since } : null),
    });
    // Colours follow the entity: a choice's place in its list, else the key's order.
    const order =
      field?.options.values?.map((v) => v.value) ??
      [...totals.groups.map((g) => String(g.key))].sort();
    const points: Point[] = totals.groups.map((g) => ({
      key: g.key,
      label: dated ? this.when(String(g.key), w.period) : String(g.label ?? g.key ?? '—'),
      value: Number(g.value ?? 0),
      color: `var(--chart-${(Math.max(0, order.indexOf(String(g.key))) % 8) + 1})`,
      link: !dated && g.key !== null,
    }));
    if (totals.other !== null && Number(totals.other) > 0) {
      points.push({
        key: null,
        label: this.i18n.t('widget.other'),
        value: Number(totals.other),
        color: 'var(--chart-other)',
        link: false,
      });
    }
    this.points.set(points);
    this.value.set(totals.total === null ? null : Number(totals.total));
  }

  private async list(w: WidgetDef, info: TableInfo): Promise<void> {
    const view = await this.views.view(w.list!);
    const columns = (view?.columns ?? [info.display ?? 'sys_id']).filter(
      (c) => info.fields.find((f) => f.name === c)?.read,
    );
    const { data } = await this.api.get<{
      records: Record<string, unknown>[];
      total: number | null;
    }>(path(w.table!), {
      query: { limit: 5, sort: this.viewSort, where: this.where(w), count: true },
    });
    const value = (r: Record<string, unknown>, c: string) =>
      shown(
        r,
        info.fields.find((f) => f.name === c),
        c,
        this.i18n.language(),
        (k) => this.i18n.t(k),
      );
    this.value.set(data.total);
    this.lines.set(
      data.records.map((r) => ({
        id: String(r['sys_id']),
        title: columns
          .slice(0, 2)
          .map((c) => value(r, c))
          .filter((v) => v !== '—')
          .join(' · '),
        detail: columns
          .slice(2, 5)
          .map((c) => value(r, c))
          .filter((v) => v !== '—')
          .join(' · '),
      })),
    );
  }

  private when(key: string, period: string | null): string {
    const day = new Date(`${key.slice(0, 10)}T00:00:00`);
    const language = this.i18n.language();
    if (period === 'year') {
      return String(day.getFullYear());
    }
    if (period === 'month') {
      return day.toLocaleDateString(language, { month: 'short', year: '2-digit' });
    }
    return day.toLocaleDateString(language, { day: 'numeric', month: 'short' });
  }

  protected format(value: number | null): string {
    return value === null
      ? '—'
      : value.toLocaleString(this.i18n.language(), { maximumFractionDigits: 2 });
  }

  /** The list of what the widget counts. */
  protected listLink(): { commands: string[]; query: Record<string, string> } {
    const w = this.widget();
    const link = this.answer()?.link;
    if (w.source && link?.startsWith('/') && !link.startsWith('//')) {
      const [where, search] = link.split('?');
      return { commands: [where!], query: Object.fromEntries(new URLSearchParams(search ?? '')) };
    }
    return { commands: ['/list', w.list ?? ''], query: filterQuery(w.filter) };
  }

  protected open(point: Point): void {
    const w = this.widget();
    if (!point.link || !w.group) {
      return;
    }
    const filter = and(w.filter, { field: w.group, op: 'eq', value: point.key });
    void this.router.navigate(['/list', w.list ?? ''], { queryParams: filterQuery(filter) });
  }

  // ---- the trend sparkline ----

  protected spark(): { line: string; area: string } {
    const values = this.trend();
    if (values.length < 2) {
      return { line: '', area: '' };
    }
    const max = Math.max(...values, 1);
    const pointsOf = values.map(
      (v, i) => `${(i / (values.length - 1)) * 120},${46 - (v / max) * 44}`,
    );
    return { line: pointsOf.join(' '), area: `0,48 ${pointsOf.join(' ')} 120,48` };
  }

  protected trendLabel(): string {
    return this.i18n.t('widget.trend', {
      values: this.trend()
        .map((v) => this.format(v))
        .join(', '),
    });
  }
}
