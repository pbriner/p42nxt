import {
  CdkDrag,
  CdkDragDrop,
  CdkDropList,
  CdkDropListGroup,
  moveItemInArray,
  transferArrayItem,
} from '@angular/cdk/drag-drop';
import { Component, computed, inject, input, model, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';

import { fold } from '../core/navigation';
import { I18n } from '../core/i18n';

export interface BucketItem {
  key: string;
  label: string;
  detail?: string;
}

/** Two lists side by side: what's available and what's shown, moved across and put in order.
 * Every move has a button (the keyboard's way), and is announced. */
@Component({
  selector: 'app-slushbucket',
  imports: [CdkDrag, CdkDropList, CdkDropListGroup, MatButtonModule, MatTooltipModule],
  templateUrl: './slushbucket.html',
  styleUrl: './slushbucket.scss',
})
export class Slushbucket {
  protected readonly i18n = inject(I18n);
  readonly items = input.required<BucketItem[]>();
  /** The keys shown, in order. */
  readonly shown = model.required<string[]>();

  protected readonly leftFilter = signal('');
  protected readonly rightFilter = signal('');
  protected readonly leftChosen = signal(new Set<string>());
  protected readonly rightChosen = signal(new Set<string>());
  protected readonly said = signal('');

  private readonly byKey = computed(() => new Map(this.items().map((i) => [i.key, i])));
  protected readonly available = computed(() => {
    const shown = new Set(this.shown());
    return this.items().filter((i) => !shown.has(i.key) && this.matches(i, this.leftFilter()));
  });
  protected readonly right = computed(() =>
    this.shown()
      .map((k) => this.byKey().get(k))
      .filter((i): i is BucketItem => !!i && this.matches(i, this.rightFilter())),
  );

  private matches(item: BucketItem, text: string): boolean {
    return !text || fold(item.label).includes(fold(text));
  }

  protected toggle(side: 'left' | 'right', key: string, given: Event): void {
    const event = given as MouseEvent;
    const chosen = side === 'left' ? this.leftChosen : this.rightChosen;
    chosen.update((all) => {
      const copy = new Set(event.ctrlKey || event.metaKey || event.shiftKey ? all : []);
      if (copy.has(key) && (event.ctrlKey || event.metaKey)) {
        copy.delete(key);
      } else {
        copy.add(key);
      }
      return copy;
    });
  }

  protected add(keys: string[] = [...this.leftChosen()]): void {
    const adding = keys.filter((k) => !this.shown().includes(k));
    if (adding.length) {
      this.shown.update((all) => [...all, ...adding]);
      this.said.set(this.i18n.t('slush.added', { n: adding.length }));
    }
    this.leftChosen.set(new Set());
  }

  protected remove(keys: string[] = [...this.rightChosen()]): void {
    if (keys.length) {
      this.shown.update((all) => all.filter((k) => !keys.includes(k)));
      this.said.set(this.i18n.t('slush.removed', { n: keys.length }));
    }
    this.rightChosen.set(new Set());
  }

  protected addAll(): void {
    this.add(this.available().map((i) => i.key));
  }

  protected removeAll(): void {
    this.remove(this.right().map((i) => i.key));
  }

  /** The chosen shown entries one place up or down, keeping their order among themselves. */
  protected move(by: -1 | 1): void {
    const chosen = this.rightChosen();
    const order = [...this.shown()];
    const indexes = order.map((k, i) => (chosen.has(k) ? i : -1)).filter((i) => i >= 0);
    for (const i of by < 0 ? indexes : [...indexes].reverse()) {
      const j = i + by;
      if (j < 0 || j >= order.length || chosen.has(order[j]!)) {
        continue;
      }
      [order[i], order[j]] = [order[j]!, order[i]!];
    }
    this.shown.set(order);
    this.said.set(this.i18n.t('slush.moved'));
  }

  protected canMove(by: -1 | 1): boolean {
    const chosen = this.rightChosen();
    if (!chosen.size) {
      return false;
    }
    const order = this.shown();
    return order.some(
      (k, i) =>
        chosen.has(k) && i + by >= 0 && i + by < order.length && !chosen.has(order[i + by]!),
    );
  }

  protected dropped(event: CdkDragDrop<BucketItem[]>, side: 'left' | 'right'): void {
    const item = event.item.data as BucketItem;
    if (event.previousContainer === event.container) {
      if (side === 'right' && !this.rightFilter()) {
        const order = [...this.shown()];
        moveItemInArray(order, event.previousIndex, event.currentIndex);
        this.shown.set(order);
        this.said.set(this.i18n.t('slush.moved'));
      }
      return;
    }
    if (side === 'right') {
      const order = [...this.shown()];
      transferArrayItem([item.key], order, 0, Math.min(event.currentIndex, order.length));
      this.shown.set(order);
      this.said.set(this.i18n.t('slush.added', { n: 1 }));
    } else {
      this.remove([item.key]);
    }
  }
}
