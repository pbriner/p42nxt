import { Component, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatListModule } from '@angular/material/list';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { describe } from '../core/api';
import { I18n } from '../core/i18n';
import { MIN_SEARCH, Results, Scope, Search } from '../core/search';

const PER_LIST = 20;

/** Everything found, a card per list; the query and the list are in the address. */
@Component({
  selector: 'app-search-page',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatListModule,
    MatProgressBarModule,
    MatSelectModule,
    RouterLink,
  ],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>{{ i18n.t('search.title') }}</h1>
      </header>
      <form class="form" role="search" (ngSubmit)="submit()">
        <mat-form-field appearance="outline" class="scope">
          <mat-label>{{ i18n.t('search.in') }}</mat-label>
          <mat-select name="scope" [(ngModel)]="scope">
            <mat-option value="">{{ i18n.t('search.everywhere') }}</mat-option>
            @for (s of scopes(); track s.list) {
              <mat-option [value]="s.list">{{ s.label }} · {{ s.module_label }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
        <mat-form-field appearance="outline" class="query">
          <mat-label>{{ i18n.t('search.for') }}</mat-label>
          <input matInput name="q" type="search" [(ngModel)]="q" cdkFocusInitial />
        </mat-form-field>
        <button mat-flat-button type="submit">{{ i18n.t('search.button') }}</button>
      </form>
      @if (busy()) {
        <mat-progress-bar mode="indeterminate" />
      }
      @if (error(); as message) {
        <p class="error-text" role="alert">{{ message }}</p>
      } @else if (tooShort()) {
        <p class="muted">{{ i18n.t('search.min') }}</p>
      } @else if (results(); as found) {
        @if (found.groups.length) {
          <p class="muted">
            {{
              i18n.t('search.summary', { n: total(found), q: found.query, m: found.groups.length })
            }}
          </p>
          <div class="grid">
            @for (group of found.groups; track group.list) {
              <mat-card appearance="outlined">
                <mat-card-header>
                  <mat-card-title>{{ group.label }}</mat-card-title>
                  <mat-card-subtitle>{{
                    i18n.t('search.list_count', {
                      module: group.module_label,
                      n: group.total ?? group.records.length,
                    })
                  }}</mat-card-subtitle>
                </mat-card-header>
                <mat-nav-list>
                  @for (hit of group.records; track hit.sys_id) {
                    <a mat-list-item [routerLink]="['/form', group.table, hit.sys_id]">
                      <span matListItemTitle>{{ hit.title }}</span>
                      @if (hit.subtitle) {
                        <span matListItemLine>{{ hit.subtitle }}</span>
                      }
                    </a>
                  }
                </mat-nav-list>
                <mat-card-actions>
                  <a
                    mat-button
                    [routerLink]="['/list', group.list]"
                    [queryParams]="{ q: found.query }"
                    >{{
                      i18n.t('search.show_in', {
                        n: group.total ?? group.records.length,
                        list: group.label,
                      })
                    }}</a
                  >
                </mat-card-actions>
              </mat-card>
            }
          </div>
        } @else {
          <p class="muted">{{ i18n.t('search.nothing') }}</p>
        }
      }
    </div>
  `,
  styles: `
    .form {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 12px;
    }
    .scope {
      width: 220px;
    }
    .query {
      flex: 1;
      max-width: 640px;
      min-width: 200px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 480px), 1fr));
      gap: 16px;
    }
  `,
})
export class SearchPage {
  protected readonly i18n = inject(I18n);
  private readonly search = inject(Search);
  private readonly router = inject(Router);
  private readonly params = toSignal(inject(ActivatedRoute).queryParamMap);
  protected readonly scopes = signal<Scope[]>([]);
  protected readonly results = signal<Results | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly tooShort = signal(false);
  protected q = '';
  protected scope = '';

  constructor() {
    void this.search.lists().then((s) => this.scopes.set(s));
    effect(() => {
      const params = this.params();
      this.q = params?.get('q') ?? '';
      this.scope = params?.get('scope') ?? '';
      void this.find();
    });
  }

  protected submit(): void {
    void this.router.navigate([], {
      queryParams: { q: this.q.trim() || null, scope: this.scope || null },
    });
  }

  protected total(found: Results): number {
    return found.groups.reduce((n, g) => n + (g.total ?? g.records.length), 0);
  }

  private async find(): Promise<void> {
    const q = this.q.trim();
    this.error.set(null);
    this.results.set(null);
    this.tooShort.set(q.length < MIN_SEARCH);
    if (q.length < MIN_SEARCH) {
      return;
    }
    this.busy.set(true);
    try {
      this.results.set(await this.search.find(q, this.scope || null, PER_LIST));
    } catch (error) {
      this.error.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
