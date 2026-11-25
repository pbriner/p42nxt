import { Component, computed, inject, input, output, signal } from '@angular/core';

import { I18n } from '../core/i18n';

export interface Point {
  key: unknown;
  label: string;
  value: number;
  /** A CSS colour (a chart token); follows the entity, never its rank. */
  color: string;
  /** Opens the list of what makes up this point; not for dates. */
  link: boolean;
  /** The second series' value at this point (plan against actual). */
  value2?: number | null;
  /** "Other": the groups past the limit (opens the records of none of the points shown). */
  other?: boolean;
}

const DONUT = 140;
const RING = 16;
const LINE_HEIGHT = 160;
const MANY_LABELS = 8;

/** Plain charts: bars, a donut, columns over time, a line. No library; a table on the card. */
@Component({
  selector: 'app-chart',
  templateUrl: './chart.html',
  styleUrl: './chart.scss',
})
export class Chart {
  protected readonly i18n = inject(I18n);
  readonly kind = input.required<'bar' | 'donut' | 'column' | 'line'>();
  readonly points = input.required<Point[]>();
  readonly accent = input('var(--chart-1)');
  readonly total = input<number | null>(null);
  readonly compact = input(false);
  /** The two series' names, when the points carry a second value. */
  readonly series = input<[string, string] | null>(null);
  readonly picked = output<Point>();
  protected readonly hover = signal<number | null>(null);

  protected readonly max = computed(() =>
    Math.max(0, ...this.points().flatMap((p) => [p.value, p.value2 ?? 0])),
  );
  protected readonly min = computed(() => Math.min(0, ...this.points().map((p) => p.value)));
  protected readonly sum = computed(
    () => this.total() ?? this.points().reduce((n, p) => n + p.value, 0),
  );

  protected format(value: number): string {
    return value.toLocaleString(this.i18n.language(), { maximumFractionDigits: 2 });
  }

  protected compactNumber(value: number): string {
    return value.toLocaleString(this.i18n.language(), {
      notation: 'compact',
      maximumFractionDigits: 1,
    });
  }

  protected share(p: Point): string {
    const all = this.sum();
    return all ? `${Math.round((p.value / all) * 100)} %` : '';
  }

  protected width(p: Point): string {
    return `${this.max() ? (p.value / this.max()) * 100 : 0}%`;
  }

  protected width2(p: Point): string {
    return `${this.max() ? ((p.value2 ?? 0) / this.max()) * 100 : 0}%`;
  }

  protected height2(p: Point): string {
    return `${this.max() ? ((p.value2 ?? 0) / this.max()) * 100 : 0}%`;
  }

  // ---- donut ----

  protected readonly size = DONUT;
  protected readonly ring = RING;
  protected readonly radius = (DONUT - RING) / 2;
  protected readonly circumference = 2 * Math.PI * ((DONUT - RING) / 2);

  /** Each slice as a dash along the ring: its length and where it starts. */
  protected readonly slices = computed(() => {
    const all = this.points().reduce((n, p) => n + Math.max(0, p.value), 0);
    let start = 0;
    return this.points().map((p) => {
      const length = all ? (Math.max(0, p.value) / all) * this.circumference : 0;
      const slice = {
        point: p,
        dash: `${Math.max(0, length - 2)} ${this.circumference}`,
        offset: -start,
      };
      start += length;
      return slice;
    });
  });

  // ---- columns ----

  protected height(p: Point): string {
    return `${this.max() ? Math.max(2, (p.value / this.max()) * 100) : 0}%`;
  }

  /** All labels up to 8 columns, else about 6 spread over the axis. */
  protected labelled(index: number): boolean {
    const count = this.points().length;
    if (count <= MANY_LABELS) {
      return true;
    }
    const step = Math.ceil(count / 6);
    return index % step === 0 || index === count - 1;
  }

  // ---- line ----

  protected readonly lineHeight = LINE_HEIGHT;

  private y(value: number): number {
    const range = this.max() - this.min() || 1;
    return 100 - ((value - this.min()) / range) * 100;
  }

  private x(index: number): number {
    const count = this.points().length;
    return count > 1 ? (index / (count - 1)) * 100 : 50;
  }

  protected readonly line = computed(() =>
    this.points()
      .map((p, i) => `${this.x(i)},${this.y(p.value)}`)
      .join(' '),
  );
  protected readonly area = computed(() => {
    const count = this.points().length;
    return count ? `0,100 ${this.line()} 100,100` : '';
  });
  protected readonly grid = computed(() => {
    const top = this.max();
    const bottom = this.min();
    return [top, (top + bottom) / 2, bottom].map((v) => ({ value: v, y: this.y(v) }));
  });
  protected readonly zero = computed(() => (this.min() < 0 && this.max() > 0 ? this.y(0) : null));

  protected at(index: number): { x: number; y: number } {
    return { x: this.x(index), y: this.y(this.points()[index]?.value ?? 0) };
  }

  protected track(event: PointerEvent, box: HTMLElement): void {
    const rect = box.getBoundingClientRect();
    const count = this.points().length;
    if (!count || !rect.width) {
      return;
    }
    const index = Math.round(((event.clientX - rect.left) / rect.width) * (count - 1));
    this.hover.set(Math.max(0, Math.min(count - 1, index)));
  }
}
