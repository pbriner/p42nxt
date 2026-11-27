import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { Router } from '@angular/router';

import { I18n } from '../core/i18n';

/** A thing on a map: its level is where it stands from the one the map is about (0): what it
 *  needs to the right (1, 2 …), what needs it to the left (-1, -2 …). */
export interface GraphNode {
  id: string;
  label: string;
  kind?: string;
  status?: string;
  level: number;
  link?: string | null;
  tone?: 'good' | 'bad' | 'muted' | null;
}

export interface GraphEdge {
  from: string;
  to: string;
  label?: string;
}

interface View {
  x: number;
  y: number;
  width: number;
  height: number;
}

const COLUMN = 190;
const ROW = 64;
const BOX = { width: 160, height: 44 };
const MARGIN = 16;

/** A layered map drawn in SVG, no library: one column per level, links as lines between them
 *  with their labels; each box opens its record. A list of the same follows for screen readers. */
@Component({
  selector: 'app-graph',
  imports: [MatButtonModule],
  template: `
    @if (zoomable()) {
      <div class="tools" role="group" [attr.aria-label]="i18n.t('graph.view')">
        <button
          mat-icon-button
          type="button"
          [attr.aria-label]="i18n.t('graph.zoom_in')"
          (click)="zoom(0.8)"
        >
          <span class="material-symbols-outlined" aria-hidden="true">zoom_in</span>
        </button>
        <button
          mat-icon-button
          type="button"
          [attr.aria-label]="i18n.t('graph.zoom_out')"
          (click)="zoom(1.25)"
        >
          <span class="material-symbols-outlined" aria-hidden="true">zoom_out</span>
        </button>
        <button
          mat-icon-button
          type="button"
          [attr.aria-label]="i18n.t('graph.fit')"
          (click)="fit()"
        >
          <span class="material-symbols-outlined" aria-hidden="true">fit_screen</span>
        </button>
      </div>
    }
    <div class="scroll" [class.zoomable]="zoomable()">
      <svg
        #svg
        [attr.width]="zoomable() ? '100%' : size().width"
        [attr.height]="zoomable() ? '100%' : size().height"
        [attr.viewBox]="shownBox()"
        role="img"
        [attr.aria-label]="summary()"
        (wheel)="wheel($event, svg)"
        (pointerdown)="grab($event, svg)"
        (pointermove)="drag($event, svg)"
        (pointerup)="release($event, svg)"
        (pointercancel)="release($event, svg)"
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" class="arrowhead" />
          </marker>
        </defs>
        @for (e of lines(); track $index) {
          <line
            class="edge"
            [attr.x1]="e.x1"
            [attr.y1]="e.y1"
            [attr.x2]="e.x2"
            [attr.y2]="e.y2"
            marker-end="url(#arrow)"
          />
          @if (e.label) {
            <text
              class="edge-label"
              [attr.x]="(e.x1 + e.x2) / 2"
              [attr.y]="(e.y1 + e.y2) / 2 - 4"
              text-anchor="middle"
            >
              {{ e.label }}
            </text>
          }
        }
        @for (n of placed(); track n.node.id) {
          <g
            class="node"
            [class.root]="n.node.level === 0"
            [attr.data-tone]="n.node.tone ?? null"
            [attr.transform]="'translate(' + n.x + ',' + n.y + ')'"
            [attr.tabindex]="n.node.link ? 0 : null"
            (click)="open(n.node)"
            (keydown.enter)="open(n.node)"
          >
            <rect [attr.width]="box.width" [attr.height]="box.height" rx="8" />
            <text x="10" y="18" class="label">{{ cut(n.node.label, 22) }}</text>
            <text x="10" y="34" class="kind">
              {{ cut(n.node.kind ?? '', 18) }}{{ n.node.status ? ' · ' + n.node.status : '' }}
            </text>
          </g>
        }
      </svg>
    </div>
    <ul class="cdk-visually-hidden">
      @for (e of edges(); track $index) {
        <li>{{ name(e.from) }} {{ e.label ?? '→' }} {{ name(e.to) }}</li>
      }
    </ul>
  `,
  styles: `
    :host {
      display: block;
      position: relative;
    }
    .scroll {
      overflow-x: auto;
    }
    /* Full screen: the map fills the space, moved and zoomed instead of scrolled. */
    :host:has(.zoomable) {
      height: 100%;
    }
    .scroll.zoomable {
      overflow: hidden;
      height: 100%;
      touch-action: none;
      cursor: grab;
    }
    .scroll.zoomable:active {
      cursor: grabbing;
    }
    .tools {
      position: absolute;
      top: 0;
      right: 0;
      display: flex;
      gap: 4px;
      border-radius: var(--app-radius);
      background: var(--mat-sys-surface-container);
    }
    .edge {
      stroke: var(--mat-sys-outline);
      stroke-width: 1.5;
    }
    .arrowhead {
      fill: var(--mat-sys-outline);
    }
    .edge-label {
      font: var(--mat-sys-label-small);
      fill: var(--mat-sys-on-surface-variant);
    }
    .node rect {
      fill: var(--mat-sys-surface-container-high);
      stroke: var(--mat-sys-outline-variant);
    }
    .node.root rect {
      stroke: var(--mat-sys-primary);
      stroke-width: 2;
    }
    .node[data-tone='bad'] rect {
      stroke: var(--mat-sys-error);
    }
    .node[data-tone='muted'] {
      opacity: 0.6;
    }
    .node[tabindex] {
      cursor: pointer;
    }
    .node[tabindex]:focus-visible rect {
      outline: 2px solid var(--mat-sys-primary);
    }
    .label {
      font: var(--mat-sys-label-large);
      fill: var(--mat-sys-on-surface);
    }
    .kind {
      font: var(--mat-sys-label-small);
      fill: var(--mat-sys-on-surface-variant);
    }
  `,
})
export class Graph {
  protected readonly i18n = inject(I18n);
  private readonly router = inject(Router);
  readonly nodes = input.required<GraphNode[]>();
  readonly edges = input<GraphEdge[]>([]);
  /** Full screen: zoom (wheel, buttons), drag to move, fit to the space. */
  readonly zoomable = input(false);
  protected readonly box = BOX;
  /** The part of the map shown (SVG units); null: all of it. */
  private readonly view = signal<View | null>(null);
  private grabbed: { x: number; y: number; view: View; moved: boolean } | null = null;
  private dragged = false;

  protected readonly shownBox = computed(() => {
    const v = this.view() ?? this.whole();
    return `${v.x} ${v.y} ${v.width} ${v.height}`;
  });

  constructor() {
    // Another map (another record chosen): seen whole again.
    effect(() => {
      this.nodes();
      this.view.set(null);
    });
  }

  private whole(): View {
    return { x: 0, y: 0, ...this.size() };
  }

  /** SVG units per screen pixel: the view is fitted into the box keeping its proportions. */
  private scale(svg: Element, v: View): number {
    const rect = svg.getBoundingClientRect();
    return Math.max(v.width / (rect.width || 1), v.height / (rect.height || 1));
  }

  /** Below 1 zooms in. Around a point (SVG units), else the middle; never past 8× either way. */
  protected zoom(by: number, at?: { x: number; y: number }): void {
    const v = this.view() ?? this.whole();
    const whole = this.whole();
    const width = Math.min(whole.width * 8, Math.max(whole.width / 8, v.width * by));
    const factor = width / v.width;
    const cx = at?.x ?? v.x + v.width / 2;
    const cy = at?.y ?? v.y + v.height / 2;
    this.view.set({
      x: cx - (cx - v.x) * factor,
      y: cy - (cy - v.y) * factor,
      width,
      height: v.height * factor,
    });
  }

  protected fit(): void {
    this.view.set(null);
  }

  protected wheel(event: WheelEvent, svg: Element): void {
    if (!this.zoomable()) {
      return;
    }
    event.preventDefault();
    const v = this.view() ?? this.whole();
    const rect = svg.getBoundingClientRect();
    const k = this.scale(svg, v);
    // The point under the pointer stays there (the view is centred in the box).
    const at = {
      x: v.x + v.width / 2 + (event.clientX - rect.left - rect.width / 2) * k,
      y: v.y + v.height / 2 + (event.clientY - rect.top - rect.height / 2) * k,
    };
    this.zoom(event.deltaY > 0 ? 1.15 : 1 / 1.15, at);
  }

  protected grab(event: PointerEvent, svg: Element): void {
    if (!this.zoomable() || event.button !== 0) {
      return;
    }
    this.grabbed = {
      x: event.clientX,
      y: event.clientY,
      view: this.view() ?? this.whole(),
      moved: false,
    };
    this.dragged = false;
    svg.setPointerCapture?.(event.pointerId);
  }

  protected drag(event: PointerEvent, svg: Element): void {
    const from = this.grabbed;
    if (!from) {
      return;
    }
    const dx = event.clientX - from.x;
    const dy = event.clientY - from.y;
    if (!from.moved && Math.hypot(dx, dy) < 4) {
      return; // a click on a box, not a drag
    }
    from.moved = true;
    const k = this.scale(svg, from.view);
    this.view.set({ ...from.view, x: from.view.x - dx * k, y: from.view.y - dy * k });
  }

  protected release(event: PointerEvent, svg: Element): void {
    this.dragged = this.grabbed?.moved ?? false;
    this.grabbed = null;
    svg.releasePointerCapture?.(event.pointerId);
  }

  private readonly levels = computed(() => {
    const all = this.nodes().map((n) => n.level);
    return { min: Math.min(0, ...all), max: Math.max(0, ...all) };
  });

  protected readonly placed = computed(() => {
    const { min } = this.levels();
    const rows = new Map<number, number>();
    return this.nodes().map((node) => {
      const row = rows.get(node.level) ?? 0;
      rows.set(node.level, row + 1);
      return { node, x: MARGIN + (node.level - min) * COLUMN, y: MARGIN + row * ROW };
    });
  });

  protected readonly size = computed(() => {
    const { min, max } = this.levels();
    const tallest = Math.max(1, ...this.countPerLevel().values());
    return {
      width: MARGIN * 2 + (max - min) * COLUMN + BOX.width,
      height: MARGIN * 2 + (tallest - 1) * ROW + BOX.height,
    };
  });

  private countPerLevel(): Map<number, number> {
    const found = new Map<number, number>();
    for (const n of this.nodes()) {
      found.set(n.level, (found.get(n.level) ?? 0) + 1);
    }
    return found;
  }

  protected readonly lines = computed(() => {
    const at = new Map(this.placed().map((p) => [p.node.id, p]));
    return this.edges()
      .map((e) => {
        const a = at.get(e.from);
        const b = at.get(e.to);
        if (!a || !b) {
          return null;
        }
        // From the side facing the other box.
        const forward = a.x <= b.x;
        return {
          x1: a.x + (forward ? BOX.width : 0),
          y1: a.y + BOX.height / 2,
          x2: b.x + (forward ? 0 : BOX.width),
          y2: b.y + BOX.height / 2,
          label: e.label,
        };
      })
      .filter((l): l is NonNullable<typeof l> => l !== null);
  });

  protected readonly summary = computed(() =>
    this.nodes()
      .map((n) => n.label)
      .join(', '),
  );

  protected name(id: string): string {
    return this.nodes().find((n) => n.id === id)?.label ?? id;
  }

  protected cut(text: string, most: number): string {
    return text.length > most ? `${text.slice(0, most - 1)}…` : text;
  }

  protected open(node: GraphNode): void {
    if (this.dragged) {
      this.dragged = false;
      return; // the end of a drag over a box
    }
    if (node.link && node.link.startsWith('/') && !node.link.startsWith('//')) {
      void this.router.navigateByUrl(node.link);
    }
  }
}
