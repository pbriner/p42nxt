import { Component, computed, inject, input } from '@angular/core';
import { Router } from '@angular/router';

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

const COLUMN = 190;
const ROW = 64;
const BOX = { width: 160, height: 44 };
const MARGIN = 16;

/** A layered map drawn in SVG, no library: one column per level, links as lines between them
 *  with their labels; each box opens its record. A list of the same follows for screen readers. */
@Component({
  selector: 'app-graph',
  template: `
    <div class="scroll">
      <svg
        [attr.width]="size().width"
        [attr.height]="size().height"
        [attr.viewBox]="'0 0 ' + size().width + ' ' + size().height"
        role="img"
        [attr.aria-label]="summary()"
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
    .scroll {
      overflow-x: auto;
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
  private readonly router = inject(Router);
  readonly nodes = input.required<GraphNode[]>();
  readonly edges = input<GraphEdge[]>([]);
  protected readonly box = BOX;

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
    if (node.link && node.link.startsWith('/') && !node.link.startsWith('//')) {
      void this.router.navigateByUrl(node.link);
    }
  }
}
