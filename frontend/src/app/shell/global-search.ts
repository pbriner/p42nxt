import { Component, ElementRef, effect, inject, input, signal, viewChild } from '@angular/core';
import {
  MatAutocompleteModule,
  MatAutocompleteSelectedEvent,
} from '@angular/material/autocomplete';
import { MatButtonModule } from '@angular/material/button';
import { MatSelectModule } from '@angular/material/select';
import { Router } from '@angular/router';

import { I18n } from '../core/i18n';
import { Found, Navigation, target } from '../core/navigation';
import { Group, Hit, MIN_SEARCH, Results, Scope, Search } from '../core/search';

type Choice =
  { kind: 'nav'; found: Found } | { kind: 'record'; group: Group; hit: Hit } | { kind: 'all' };

const EVERYWHERE = '';
const DEBOUNCE_MS = 250;
const GO_TO = 5;

/** The search bar: lists and navigation items as you type, everything on the search page. */
@Component({
  selector: 'app-global-search',
  imports: [MatAutocompleteModule, MatButtonModule, MatSelectModule],
  templateUrl: './global-search.html',
  styleUrl: './global-search.scss',
})
export class GlobalSearch {
  protected readonly i18n = inject(I18n);
  private readonly navigation = inject(Navigation);
  private readonly search = inject(Search);
  private readonly router = inject(Router);
  /** The list shown on the page, if any: the search starts in it. */
  readonly pageList = input<string | null>(null);
  private readonly field = viewChild.required<ElementRef<HTMLInputElement>>('field');

  protected readonly scopes = signal<Scope[]>([]);
  protected readonly scope = signal(EVERYWHERE);
  protected readonly text = signal('');
  protected readonly goTo = signal<Found[]>([]);
  protected readonly results = signal<Results | null>(null);
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running: AbortController | null = null;

  constructor() {
    void this.search.lists().then((s) => this.scopes.set(s));
    effect(() => {
      const list = this.pageList();
      const known = this.scopes().some((s) => s.list === list);
      this.scope.set(list && known ? list : EVERYWHERE);
    });
  }

  focus(): void {
    this.field().nativeElement.focus();
  }

  protected typed(value: string): void {
    this.text.set(value);
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => void this.find(), DEBOUNCE_MS);
  }

  protected changeScope(scope: string): void {
    this.scope.set(scope);
    void this.find();
  }

  private async find(): Promise<void> {
    const q = this.text().trim();
    this.running?.abort();
    if (q.length < MIN_SEARCH) {
      this.goTo.set([]);
      this.results.set(null);
      return;
    }
    this.goTo.set(this.scope() ? [] : this.navigation.matching(q).slice(0, GO_TO));
    const running = new AbortController();
    this.running = running;
    try {
      const found = await this.search.find(q, this.scope() || null, 5, running.signal);
      if (this.running === running) {
        this.results.set(found);
      }
    } catch {
      if (this.running === running) {
        this.results.set({ query: q, groups: [] });
      }
    }
  }

  protected total(): number {
    return (this.results()?.groups ?? []).reduce((n, g) => n + (g.total ?? g.records.length), 0);
  }

  protected chosen(event: MatAutocompleteSelectedEvent): void {
    const choice = event.option.value as Choice;
    if (choice.kind === 'nav') {
      const to = target(choice.found.item);
      void this.router.navigate(to.commands, { queryParams: to.query });
    } else if (choice.kind === 'record') {
      void this.router.navigate(['/form', choice.group.table, choice.hit.sys_id]);
    } else {
      this.showAll();
      return;
    }
    this.clear();
  }

  protected showAll(): void {
    const q = this.text().trim();
    void this.router.navigate(['/search'], {
      queryParams: { q, scope: this.scope() || null },
    });
    this.clear();
  }

  protected enter(event: Event, panelHasActive: boolean): void {
    if (!panelHasActive) {
      event.preventDefault();
      this.showAll();
    }
  }

  private clear(): void {
    this.text.set('');
    this.field().nativeElement.value = '';
    this.goTo.set([]);
    this.results.set(null);
    this.field().nativeElement.blur();
  }

  protected display = (): string => '';
}
